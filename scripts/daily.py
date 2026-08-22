"""일일 스냅샷 생성 (scripts/daily.ts 포팅).

앱과 같은 run_analysis를 10개 저널 전체에 돌리고, Gemini 동향 분석·아이디어 제안을
덧붙여 data/daily.json에 저장한다. 실행: python scripts/daily.py
"""
from __future__ import annotations

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


def env(name: str) -> str:
    return os.environ.get(name, "").strip()


def log(message: str):
    print(f"[daily] {message}", flush=True)


def suggest_all(ideas, pool, trends, period, scope, creds, key, model) -> dict:
    out = {}
    for idea in ideas:  # 순차 호출: 야간 작업은 시간이 있고, 병렬 호출은 속도 제한에 걸리기 쉽다.
        try:
            pmids = pmids_for_idea(idea, pool, trends, IDEA_ABSTRACTS)
            if len(pmids) < ENHANCE_MIN:
                out[idea["id"]] = {"error": f"근거 초록이 {len(pmids)}편뿐이라 건너뛰었습니다."}
                continue
            out[idea["id"]] = enhance_idea(idea, pmids, trends, scope, period, creds, key, model)
            log(f"  AI 제안 완료 — {scope}: {idea['title'][:30]}…")
        except Exception as error:
            out[idea["id"]] = {"error": str(error) or "AI 제안 실패"}
            log(f"  AI 제안 실패 — {scope}: {error}")
    return out


def main():
    creds = NcbiCredentials(env("NCBI_API_KEY"), env("NCBI_TOOL_EMAIL"))
    key, model = env("GEMINI_API_KEY"), env("GEMINI_MODEL") or GEMINI_DEFAULT_MODEL
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
    trend_reports, suggestions = {}, {}
    if key:
        for fam, label in FAMILY_LABEL.items():
            members = next((f["journals"] for f in analysis["families"] if f["key"] == fam), [])
            pool = [a for a in analysis["articles"] if a["journalKey"] in members]
            try:
                trend_reports[fam] = family_trend_report(label, period, pool, analysis["trendsByFamily"].get(fam, []), creds, key, model)
                log(f"동향 분석 완료 — {fam}")
            except Exception as error:
                trend_reports[fam] = {"error": str(error) or "동향 분석 실패"}
                log(f"동향 분석 실패 — {fam}: {error}")
        suggestions.update(suggest_all(analysis["ideas"], analysis["articles"], analysis["trends"], period, "무릎 전체", creds, key, model))
        for fam, label in FAMILY_LABEL.items():
            ideas = analysis["ideasByFamily"].get(fam) or []
            if not ideas:
                continue
            members = next((f["journals"] for f in analysis["families"] if f["key"] == fam), [])
            pool = [a for a in analysis["articles"] if a["journalKey"] in members]
            suggestions.update(suggest_all(ideas, pool, analysis["trendsByFamily"].get(fam, []), period, label, creds, key, model))

    snapshot = {"generatedAt": datetime.now(timezone.utc).isoformat(), "model": model if key else None,
                "familyLabels": FAMILY_LABEL, "trendReports": trend_reports, "suggestions": suggestions, "analysis": analysis}
    out = Path("data/daily.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(snapshot, ensure_ascii=False)
    out.write_text(text + "\n", "utf-8")
    log(f"저장 완료: {out} ({len(text.encode()) / 1048576:.2f}MB)")
    log(f"AI 제안 {len(suggestions)}건, 동향 분석 {len(trend_reports)}건")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"[daily] 실패: {error}", file=sys.stderr)
        sys.exit(1)
