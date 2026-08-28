"""백테스트 실행: 과거 창으로 아이디어를 만들고 미래 창으로 채점한다.

실행 예:
    # 판정 없이 통계 공백만 (NCBI만 쓰므로 무료)
    python scripts/backtest.py

    # 창을 직접 지정
    python scripts/backtest.py --past-from 2023-01-01 --past-to 2023-12-31

    # AI 판정 포함 (GEMINI_API_KEY 필요 · 판정은 캐시되므로 재실행은 무료)
    python scripts/backtest.py --judge

비용은 --judge를 켰을 때만, 그것도 캐시에 없는 판정에만 든다. 처음에는 판정 없이
돌려 "탐지기 자체가 미래를 예측하는가"를 공짜로 확인하고, 그다음 --judge를 붙여
"판정기가 opportunity와 structural을 실제로 가르는가"를 확인하는 순서를 권한다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar import analysis as analysis_module  # noqa: E402
from radar.analysis import JOURNAL_ORDER, AnalysisError, run_analysis  # noqa: E402
from radar.backtest import report_markdown, score_all, summarize  # noqa: E402
from radar.ncbi import NcbiCredentials  # noqa: E402

OUT_DIR = Path("data/backtest")
# run_analysis의 기간 상한(730일) 안에서 안전하게 자르는 조각 길이.
CHUNK_DAYS = 365
# 백테스트 기본 후보 수. 운영값(계열당 6개)은 비용 때문에 낮춰 둔 것이라, 그대로
# 쓰면 채점 대상이 6개뿐이어서 어떤 비율도 통계로 읽을 수 없다. 여기서만 올린다.
# 다양성 상한(IDEA_PER_KIND·IDEA_PER_LEAD)이 따로 걸려 있어 실제로는 15개 안팎에서
# 멈춘다 — 그 상한까지는 받아 두자는 값이다.
DEFAULT_CANDIDATES = 40
# 판정 1건(주 판정자 5회 + 교차 1회)의 대략적 비용. 실행 전 안내용.
WON_PER_JUDGMENT = 109


def _credentials() -> NcbiCredentials:
    return NcbiCredentials(api_key=os.environ.get("NCBI_API_KEY", "").strip(),
                           email=os.environ.get("NCBI_TOOL_EMAIL", "").strip())


def _progress(label: str):
    last = {"text": ""}

    def report(fraction: float, message: str):
        # run_analysis는 진행 콜백을 자주 부른다. 같은 메시지는 한 번만 찍는다.
        if message != last["text"]:
            last["text"] = message
            print(f"  [{label}] {round(fraction * 100):3d}% {message}", flush=True)
    return report


def _chunks(date_from: str, date_to: str) -> list[tuple[str, str]]:
    """미래 창이 2년을 넘을 수 있으므로 run_analysis의 상한 아래로 자른다."""
    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    out = []
    cursor = start
    while cursor <= end:
        stop = min(cursor + timedelta(days=CHUNK_DAYS - 1), end)
        out.append((cursor.isoformat(), stop.isoformat()))
        cursor = stop + timedelta(days=1)
    return out


def collect(journals: list[str], date_from: str, date_to: str,
            credentials: NcbiCredentials, label: str) -> dict:
    """조각별로 run_analysis를 부르고 초록을 PMID로 합친다.

    수집 도중 결과가 밀려 조각 경계에서 같은 논문이 두 번 올 수 있으므로
    PMID 기준으로 중복을 제거한다. 트렌드·아이디어는 조각별 값이라 합칠 수
    없고, 여기서는 초록(분류 포함)만 쓴다.
    """
    merged: dict[str, dict] = {}
    capped = False
    for n, (chunk_from, chunk_to) in enumerate(_chunks(date_from, date_to), 1):
        print(f"[{label}] 조각 {n}: {chunk_from} – {chunk_to}")
        try:
            result = run_analysis(journals, chunk_from, chunk_to, "", credentials,
                                  progress=_progress(label))
        except AnalysisError as error:
            if getattr(error, "status", 0) == 404:
                print("  이 조각에는 문헌이 없습니다. 건너뜁니다.")
                continue
            raise
        capped = capped or result.get("capped", False)
        for article in result["articles"]:
            merged.setdefault(article["pmid"], article)
    return {"articles": list(merged.values()), "capped": capped}


def judge_ideas(ideas: list[dict], past_articles: list[dict],
                past_from: str, past_to: str) -> tuple[dict, int]:
    """선택적 AI 판정. 캐시에 있으면 호출하지 않는다. 반환: (판정 사전, 새 호출 건수)."""
    from radar.cache import get as cache_get
    from radar.cache import put as cache_put
    from radar.judge import build_panel, judge_panel, judgment_cache_key, titles_for_idea

    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not gemini_key:
        raise SystemExit("--judge에는 GEMINI_API_KEY 환경변수가 필요합니다.")
    # 판정단 구성은 야간 작업과 똑같이 맞춘다. 다르면 판정이 서로 비교되지 않고,
    # 캐시 열쇠에 판정단 이름이 들어가므로 캐시도 공유되지 않는다.
    panel = build_panel(gemini_key,
                        os.environ.get("GEMINI_MODEL", ""),
                        os.environ.get("GEMINI_JUDGE_MODEL_B", ""),
                        os.environ.get("OPENAI_API_KEY", "").strip(),
                        os.environ.get("OPENAI_JUDGE_MODEL", ""))
    # 범위 문구는 판정 프롬프트에 그대로 들어간다. 백테스트 전용 문구를 써서
    # 운영 판정과 섞이지 않게 한다(같은 아이디어라도 다른 판정이다).
    scope = "백테스트 · 선택 저널 전체"
    period = f"{past_from}–{past_to}"

    judgments: dict[str, dict] = {}
    fresh = 0
    for n, idea in enumerate(ideas, 1):
        titles = titles_for_idea(idea, past_articles)
        key = judgment_cache_key(idea, scope, period, titles, panel)
        cached = cache_get(key)
        if cached:
            judgments[idea["id"]] = cached
            print(f"[판정 {n}/{len(ideas)}] {idea['id']} — 캐시 재사용")
            continue
        print(f"[판정 {n}/{len(ideas)}] {idea['id']} — 호출 중")
        try:
            judgment = judge_panel(idea, titles, scope, period, panel)
        except Exception as error:
            print(f"  판정 실패: {error}")
            continue
        cache_put(key, judgment)
        judgments[idea["id"]] = judgment
        fresh += 1
    return judgments, fresh


def main():
    today = date.today()
    default_past_to = today - timedelta(days=730)
    default_past_from = default_past_to - timedelta(days=364)
    parser = argparse.ArgumentParser(description="ArthroScope 백테스트")
    parser.add_argument("--past-from", default=default_past_from.isoformat(),
                        help="아이디어 생성 창 시작일 (기본: 3년 전부터 1년)")
    parser.add_argument("--past-to", default=default_past_to.isoformat())
    parser.add_argument("--future-from", default="",
                        help="채점 창 시작일 (기본: 과거 창 종료 다음 날)")
    parser.add_argument("--future-to", default=today.isoformat())
    parser.add_argument("--journals", default=",".join(JOURNAL_ORDER),
                        help="쉼표 구분 저널 키 (기본: 전체)")
    parser.add_argument("--judge", action="store_true",
                        help="AI 판정 포함 (GEMINI_API_KEY 필요 · 캐시된 판정은 재호출 없음)")
    parser.add_argument("--candidates", type=int, default=DEFAULT_CANDIDATES,
                        help=f"과거 창에서 만들 후보 수 상한 (기본 {DEFAULT_CANDIDATES}). "
                             "운영값 6은 채점 표본으로 너무 작다.")
    parser.add_argument("--cap", type=int, default=0,
                        help="조각당 초록 상한 재정의. 채점은 전량이 정확하므로 넉넉히 (예: 20000)")
    parser.add_argument("--yes", action="store_true", help="--judge 비용 확인 프롬프트를 건너뛴다")
    args = parser.parse_args()

    journals = [k for k in JOURNAL_ORDER if k in args.journals.split(",")]
    if not journals:
        raise SystemExit("--journals에 알 수 없는 저널 키만 있습니다.")
    future_from = args.future_from or (date.fromisoformat(args.past_to) + timedelta(days=1)).isoformat()
    if args.cap:
        # run_analysis는 모듈 전역 상한을 참조한다. 백테스트 채점은 균등 표본이 아니라
        # 전량이 정확하므로, 여기서만 상한을 올린다. 앱·일일 작업에는 영향이 없다.
        analysis_module.MAX_TOTAL_ARTICLES = args.cap
    # 후보 수도 같은 이유로 이 프로세스에서만 올린다.
    analysis_module.IDEA_MAX = args.candidates

    credentials = _credentials()
    print(f"과거 창 {args.past_from} – {args.past_to} → 미래 창 {future_from} – {args.future_to}")
    print(f"저널: {', '.join(journals)} · 후보 상한 {args.candidates} · "
          f"판정: {'포함' if args.judge else '없음 (통계만)'}")
    if not credentials.api_key:
        print("경고: NCBI_API_KEY가 없습니다. 수집이 순차로 돌아 매우 느립니다.")

    # 1. 과거 창: 아이디어 생성 (기존 파이프라인 그대로)
    past = run_analysis(journals, args.past_from, args.past_to, "", credentials,
                        progress=_progress("과거"))
    ideas = past["ideas"]
    if past.get("capped"):
        print("주의: 과거 창이 초록 상한에 걸렸습니다. --cap으로 상한을 올리면 더 정확합니다.")
    print(f"과거 창: 초록 {past['analyzed']:,}편 · 아이디어 후보 {len(ideas)}개")
    if not ideas:
        raise SystemExit("과거 창에서 아이디어가 생성되지 않았습니다. 창을 넓혀 보세요.")

    # 2. 선택적 AI 판정 (여기서만 비용 발생 · 캐시 재사용)
    judgments: dict[str, dict] = {}
    fresh_calls = 0
    if args.judge:
        cost = len(ideas) * WON_PER_JUDGMENT
        print(f"\n판정 대상 {len(ideas)}개 · 캐시가 모두 비었다면 최대 {len(ideas) * 6}호출 "
              f"· 약 {cost:,}원")
        if not args.yes and input("계속할까요? [y/N] ").strip().lower() not in ("y", "yes"):
            raise SystemExit("중단했습니다.")
        judgments, fresh_calls = judge_ideas(ideas, past["articles"], args.past_from, args.past_to)
        print(f"판정 {len(judgments)}건 (새 호출 {fresh_calls}건 · 나머지 캐시)")

    # 3. 미래 창: 채점용 코퍼스 (조각 수집 후 PMID 병합)
    future = collect(journals, future_from, args.future_to, credentials, "미래")
    if future["capped"]:
        print("주의: 미래 창 조각이 초록 상한에 걸렸습니다. 채움률이 과소평가될 수 있습니다. "
              "--cap으로 상한을 올려 다시 돌리세요.")
    print(f"미래 창: 초록 {len(future['articles']):,}편")

    # 4. 채점 및 보고서
    outcomes = score_all(ideas, judgments, future["articles"], future_from, args.future_to)
    summary = summarize(outcomes)
    meta = {"pastFrom": args.past_from, "pastTo": args.past_to,
            "futureFrom": future_from, "futureTo": args.future_to,
            "pastArticles": past["analyzed"], "futureArticles": len(future["articles"]),
            "ideas": len(ideas), "judged": len(judgments), "freshJudgeCalls": fresh_calls,
            "candidateCap": args.candidates, "journals": journals,
            "generatedAt": datetime.now().astimezone().isoformat()}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"backtest-{args.past_from}--{args.past_to}"
    json_path = OUT_DIR / f"{stem}.json"
    md_path = OUT_DIR / f"{stem}.md"
    json_path.write_text(json.dumps({"meta": meta, "summary": summary, "outcomes": outcomes,
                                     "judgments": judgments}, ensure_ascii=False, indent=2), "utf-8")
    md_path.write_text(report_markdown(summary, outcomes, meta), "utf-8")

    print()
    print("=" * 62)
    for verdict, row in summary["byVerdict"].items():
        print(f"  {verdict:12s} {row['count']:3d}건 · 기준A {row['filledByRatio']}건 "
              f"· 기준B {row['filledByCount']}건 · 둘 중 하나 {row['filledEither']}건")
    if summary["skipped"]:
        print(f"  건너뜀 {len(summary['skipped'])}건 (보고서 참조)")
    if summary["scored"] < 10:
        print(f"  주의: 채점 {summary['scored']}개는 통계로 읽기에 부족합니다.")
    print("=" * 62)
    print(f"보고서: {md_path}")
    print(f"원자료: {json_path}")


if __name__ == "__main__":
    main()
