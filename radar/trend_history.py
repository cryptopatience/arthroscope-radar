"""AI 동향 분석 이력 — 주차별로 쌓아 두고 나중에 다시 읽는다.

동향 분석은 주 1회 Gemini가 쓰는 글이고, 지금까지는 data/daily.json 안에만 있었다.
그 파일은 매일 통째로 덮어써지므로 지난주 분석은 다음 갱신과 함께 사라졌다.
"감염 쪽 서술이 지난달과 어떻게 달라졌나" 같은 질문에 답할 방법이 없었다.

여기에 주차(ISO 주) 단위로 따로 쌓는다. 같은 주에 여러 번 돌면 마지막 것이 그 주를
대표한다 — 같은 주의 두 실행은 거의 같은 코퍼스를 보므로 둘 다 남길 이유가 없다.

계산이 아니라 저장만 하므로 비용이 없다. 항목 하나가 계열 두 개의 글이라 몇 KB고,
2년치를 남겨도 1MB를 넘지 않는다.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

HISTORY_PATH = Path("data/trend_history.json")
# 남길 주차 수. 2년치면 추세를 보기 충분하고 파일도 1MB 아래에 머문다.
WEEKS_KEEP = 104


def week_key(stamp: str) -> str:
    """ISO 8601 주차 문자열. 화면의 주차 선택에 그대로 쓴다(예: 2026-W35)."""
    try:
        year, week, _ = datetime.fromisoformat(stamp).isocalendar()
    except (TypeError, ValueError):
        return ""
    return f"{year}-W{week:02d}"


def load(path: Path = HISTORY_PATH) -> dict:
    try:
        payload = json.loads(path.read_text("utf-8"))
        return payload if isinstance(payload, dict) and isinstance(payload.get("weeks"), dict) else {"weeks": {}}
    except Exception:
        return {"weeks": {}}


def weeks(payload: dict) -> list[str]:
    """최신 주차가 앞에 오도록 정렬한 주차 목록."""
    return sorted((payload.get("weeks") or {}).keys(), reverse=True)


def record(reports: dict, refreshed_at: str, model: str, analysis: dict,
           path: Path = HISTORY_PATH) -> str:
    """이번 주 동향 분석을 이력에 넣는다. 반환값은 기록된 주차(빈 문자열이면 건너뜀).

    오류만 담긴 분석은 남기지 않는다 — 나중에 주차를 골랐을 때 "실패"만 나오면
    그 주에 분석이 없었던 것과 구분이 안 된다.
    """
    usable = {k: v for k, v in (reports or {}).items()
              if isinstance(v, dict) and not v.get("error")}
    key = week_key(refreshed_at)
    if not usable or not key:
        return ""
    payload = load(path)
    payload["weeks"][key] = {
        "at": refreshed_at,
        "model": model,
        "reports": usable,
        # 그 주가 무엇을 보고 쓴 글인지. 나중에 서술을 비교할 때 코퍼스가 얼마나
        # 달랐는지 알아야 "분석이 변했다"와 "자료가 변했다"를 구분할 수 있다.
        "corpus": {"dateFrom": analysis.get("dateFrom", ""), "dateTo": analysis.get("dateTo", ""),
                   "analyzed": analysis.get("analyzed", 0)},
    }
    for stale in sorted(payload["weeks"])[:-WEEKS_KEEP]:
        del payload["weeks"][stale]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", "utf-8")
    return key
