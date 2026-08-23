"""일일 스냅샷 생성 (scripts/daily.ts 포팅).

앱과 같은 run_analysis를 10개 저널 전체에 돌리고, Gemini 동향 분석·아이디어 제안을
덧붙여 data/daily.json에 저장한다. 실행: python scripts/daily.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from radar.analysis import JOURNAL_ORDER, JOURNALS, run_analysis  # noqa: E402
from radar.gemini import GEMINI_DEFAULT_MODEL, ENHANCE_MIN, enhance_idea, family_trend_report, pmids_for_idea  # noqa: E402
from radar.ncbi import NcbiCredentials  # noqa: E402

FAMILY_LABEL = {
    "arthroplasty": "관절성형 계열 (JOA·AT·BJJ·JBJS Am·CORR·Acta Orthop)",
    "arthroscopy": "관절경·스포츠의학 계열 (KSSTA·Arthroscopy·AJSM·OJSM)",
}
MONTHS_BACK = 12
IDEA_ABSTRACTS = 32
AI_IDEA_LIMIT = 4       # 규칙 기반 아이디어는 8개를 다 보여주되, Gemini 고도화는 점수 상위 이만큼만.
                        # 나머지는 앱에서 "AI로 고도화" 버튼으로 필요할 때 개별 호출한다.
TREND_ABSTRACTS = 30
GEMINI_WEEKDAY = 4      # 0=월 … 4=금. 초록 수집은 매일, Gemini 분석은 이 요일에만.
SNAPSHOT = Path("data/daily.json")


def env(name: str) -> str:
    return os.environ.get(name, "").strip()


def log(message: str):
    print(f"[daily] {message}", flush=True)


def load_previous() -> dict:
    """어제 스냅샷. Gemini 결과를 재사용하는 근거가 된다."""
    try:
        return json.loads(SNAPSHOT.read_text("utf-8"))
    except Exception:
        return {}


def cache_key(kind: str, ident: str, scope: str, pmids: list[str], model: str) -> str:
    """같은 아이디어에 같은 근거 초록·같은 모델이면 답도 같다. 그러면 다시 부르지 않는다."""
    raw = "|".join([kind, ident, scope, model, *sorted(pmids)])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def reuse(cached: dict | None, key: str) -> dict | None:
    if isinstance(cached, dict) and cached.get("cacheKey") == key and not cached.get("error"):
        return cached
    return None


def gemini_day(today: date, previous: dict) -> tuple[bool, str]:
    """오늘 Gemini를 돌릴지. 초록 수집 자체는 이 판단과 무관하게 매일 돈다."""
    if env("GEMINI_SKIP") == "1":
        return False, "GEMINI_SKIP=1 로 비활성화됨"
    if env("GEMINI_FORCE") == "1":
        return True, "GEMINI_FORCE=1 로 강제 실행"
    if not previous.get("suggestions") and not previous.get("trendReports"):
        return True, "이전 AI 결과가 없어 최초 1회 생성"
    if today.weekday() == GEMINI_WEEKDAY:
        return True, "금요일 정기 갱신"
    return False, f"{'월화수목금토일'[today.weekday()]}요일 — 지난 결과 재사용"


def suggest_all(ideas, pool, trends, period, scope, creds, key, model, prev: dict) -> dict:
    out = {}
    for idea in ideas:  # 순차 호출: 야간 작업은 시간이 있고, 병렬 호출은 속도 제한에 걸리기 쉽다.
        try:
            pmids = pmids_for_idea(idea, pool, trends, IDEA_ABSTRACTS)
            if len(pmids) < ENHANCE_MIN:
                out[idea["id"]] = {"error": f"근거 초록이 {len(pmids)}편뿐이라 건너뛰었습니다."}
                continue
            ck = cache_key("idea", idea["id"], scope, pmids, model)
            cached = reuse(prev.get(idea["id"]), ck)
            if cached:
                out[idea["id"]] = cached
                log(f"  캐시 재사용 — {scope}: {idea['title'][:30]}…")
                continue
            result = enhance_idea(idea, pmids, trends, scope, period, creds, key, model)
            result["cacheKey"] = ck
            out[idea["id"]] = result
            log(f"  AI 제안 완료 — {scope}: {idea['title'][:30]}…")
        except Exception as error:
            out[idea["id"]] = {"error": str(error) or "AI 제안 실패"}
            log(f"  AI 제안 실패 — {scope}: {error}")
    return out


def main():
    creds = NcbiCredentials(env("NCBI_API_KEY"), env("NCBI_TOOL_EMAIL"))
    key, model = env("GEMINI_API_KEY"), env("GEMINI_MODEL") or GEMINI_DEFAULT_MODEL
    previous = load_previous()
    if not creds.api_key:
        log("경고: NCBI_API_KEY가 없습니다. 수집이 순차로 돌아 느려집니다.")
    if not key:
        log("경고: GEMINI_API_KEY가 없습니다. 규칙 기반 결과만 저장합니다.")

    to = date.today()
    month = to.month - MONTHS_BACK
    year = to.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    frm = to.replace(year=year, month=month, day=min(to.day, 28))
    date_from, date_to = frm.isoformat(), to.isoformat()
    log(f"기간 {date_from} – {date_to}")

    analysis = run_analysis(JOURNAL_ORDER, date_from, date_to, "", creds, progress=lambda f, m: log(f"{int(f*100):3d}% {m}"))
    log(f"분석 초록 {analysis['analyzed']}편, 아이디어 {len(analysis['ideas'])}개")

    period = f"{date_from}–{date_to}"
    prev_trends = previous.get("trendReports") or {}
    prev_suggestions = previous.get("suggestions") or {}
    now = datetime.now(timezone.utc).isoformat()

    run_ai, why = gemini_day(to, previous)
    if not key:
        run_ai, why = False, "GEMINI_API_KEY 없음"

    if run_ai:
        log(f"Gemini 실행 — {why}")
        trend_reports, suggestions = {}, {}

        def family_pool(fam):
            members = next((f["journals"] for f in analysis["families"] if f["key"] == fam), [])
            return [a for a in analysis["articles"] if a["journalKey"] in members]

        for fam, label in FAMILY_LABEL.items():
            pool = family_pool(fam)
            ck = cache_key("trend", fam, label, [a["pmid"] for a in pool[:TREND_ABSTRACTS]], model)
            cached = reuse(prev_trends.get(fam), ck)
            if cached:
                trend_reports[fam] = cached
                log(f"동향 분석 캐시 재사용 — {fam}")
                continue
            try:
                report = family_trend_report(label, period, pool, analysis["trendsByFamily"].get(fam, []),
                                             creds, key, model, TREND_ABSTRACTS)
                report["cacheKey"] = ck
                trend_reports[fam] = report
                log(f"동향 분석 완료 — {fam}")
            except Exception as error:
                trend_reports[fam] = {"error": str(error) or "동향 분석 실패"}
                log(f"동향 분석 실패 — {fam}: {error}")

        top = analysis["ideas"][:AI_IDEA_LIMIT]   # ideas는 신호 강한 순으로 정렬돼 있다
        log(f"아이디어 {len(analysis['ideas'])}개 중 상위 {len(top)}개만 고도화합니다.")
        suggestions.update(suggest_all(top, analysis["articles"], analysis["trends"],
                                       period, "무릎 전체", creds, key, model, prev_suggestions))
        for fam, label in FAMILY_LABEL.items():
            ideas = (analysis["ideasByFamily"].get(fam) or [])[:AI_IDEA_LIMIT]
            if not ideas:
                continue
            suggestions.update(suggest_all(ideas, family_pool(fam), analysis["trendsByFamily"].get(fam, []),
                                           period, label, creds, key, model, prev_suggestions))
        ai_refreshed_at = now
    else:
        # 초록은 오늘 것으로 갱신하되, AI 결과는 지난 것을 그대로 들고 간다.
        log(f"Gemini 건너뜀 — {why}. 지난 AI 결과 {len(prev_suggestions)}건을 유지합니다.")
        trend_reports, suggestions = prev_trends, prev_suggestions
        ai_refreshed_at = previous.get("aiRefreshedAt") or previous.get("generatedAt")

    snapshot = {"generatedAt": now, "model": model if key else None,
                "aiRefreshedAt": ai_refreshed_at, "aiRanToday": run_ai, "aiSkipReason": None if run_ai else why,
                "familyLabels": FAMILY_LABEL, "trendReports": trend_reports, "suggestions": suggestions, "analysis": analysis}
    out = Path("data/daily.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(snapshot, ensure_ascii=False)
    out.write_text(text + "\n", "utf-8")
    log(f"저장 완료: {out} ({len(text.encode()) / 1048576:.2f}MB)")
    log(f"AI 제안 {len(suggestions)}건, 동향 분석 {len(trend_reports)}건 (오늘 Gemini 실행: {'예' if run_ai else '아니오'})")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"[daily] 실패: {error}", file=sys.stderr)
        sys.exit(1)
