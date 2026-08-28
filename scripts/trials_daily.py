"""임상시험 레이더 갱신. 일일 작업(scripts/daily.py) 뒤에 이어 붙이거나 단독 실행.

    python scripts/trials_daily.py

ClinicalTrials.gov는 무료·키 불필요라 매일 돌려도 비용이 없다. AI 호출도 없다.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar.trials import STATUS_LABEL, refresh, summarize  # noqa: E402


def main():
    payload = refresh()
    summary = summarize(payload)
    entry = payload["history"][0]
    changes = entry["changes"]
    print(f"수집 완료 — 총 {summary['total']}건 (활성 {summary['active']}건 · "
          f"완료 {summary['completed']}건 · 결과 게시 {summary['withResults']}건 · "
          f"무릎 명시 {summary['kneeExplicit']}건)")
    if entry.get("firstRun"):
        print("첫 수집입니다. 변동 감지는 다음 실행부터 시작됩니다.")
        return
    print(f"변동: 신규 {len(changes['new'])}건 · 상태 변경 {len(changes['statusChanged'])}건 · "
          f"결과 게시 {len(changes['resultsPosted'])}건 · 완료일 이동 {len(changes['completionMoved'])}건")
    for c in changes["statusChanged"][:10]:
        print(f"  [{c['nctId']}] {STATUS_LABEL.get(c['from'], c['from'])} -> "
              f"{STATUS_LABEL.get(c['to'], c['to'])} · {c['title'][:70]}")
    for c in changes["resultsPosted"][:10]:
        print(f"  [{c['nctId']}] 결과 게시됨 · {c['title'][:70]}")


if __name__ == "__main__":
    main()
