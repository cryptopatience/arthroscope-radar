"""이번 주 진료 브리핑 — 지난 한 주 초록에서 진료에 영향을 줄 만한 것만 추린다.

연구 아이디어 파이프라인과 목적이 다르다. 저쪽은 "무엇이 비어 있나"를 찾고,
여기는 "이번 주에 내 진료가 바뀔 만한 소식이 있나"를 찾는다. 같은 코퍼스를 쓰지만
묻는 것이 반대다 — 공백이 아니라 채워진 것, 새 질문이 아니라 지금의 답이다.

주당 초록이 30편 남짓이라 한 번의 호출로 전부 읽힌다. 규칙으로 먼저 거르지 않는
이유는, 연구설계 분류가 틀리면 진짜 중요한 논문이 AI 눈에 닿지도 못하기 때문이다.
30편이면 통째로 보내는 값이 더 싸고 정확하다(주당 약 80원).

초록은 반드시 PubMed에서 다시 받아온다. run_analysis가 돌려주는 초록은 420자로
잘려 있어 결론부가 없는데, 진료 영향은 대부분 결론부에 있다.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from .gemini import ABSTRACT_CHARS, call_gemini
from .ncbi import NcbiCredentials, fetch_abstracts

HISTORY_PATH = Path("data/briefing_history.json")
# 남길 주차 수. 항목 하나가 몇 KB라 2년치도 가볍다.
WEEKS_KEEP = 104
# 한 번에 읽힐 초록 수 상한. 주당 30편 남짓이라 여유가 있지만, 색인이 몰린 주에
# 프롬프트가 무한정 커지지 않게 막는다.
MAX_ARTICLES = 60
# 며칠치를 "이번 주"로 볼지.
WINDOW_DAYS = 7

AREAS = ["수술 적응증", "수술 술기", "합병증·안전", "재활·통증관리", "임플란트·기기", "환자 상담"]
STRENGTHS = ["강함", "보통", "약함"]

BRIEFING_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "pmid": {"type": "string"},
                    "headline": {"type": "string"},
                    "detail": {"type": "string"},
                    "area": {"type": "string", "enum": AREAS},
                    "strength": {"type": "string", "enum": STRENGTHS},
                    "action": {"type": "string"},
                },
                "required": ["pmid", "headline", "detail", "area", "strength", "action"],
            },
        },
    },
    "required": ["summary", "items"],
}

PROMPT = """당신은 무릎을 보는 정형외과 전문의입니다. 아래는 지난 한 주 사이 주요 저널에 실린 무릎 관련 초록 전부입니다. 이 중 **다음 주 진료에 실제로 영향을 줄 수 있는 것만** 골라 주십시오.

[기간] {period}
[초록 {count}편]

{records}

판단 기준:

1. 고르는 것은 **진료 행위가 달라질 수 있는 근거**입니다. 수술 적응증, 술기 선택, 합병증 예방, 재활·통증 프로토콜, 임플란트·기기 선택, 환자에게 설명할 숫자가 바뀌는 경우입니다.
2. **대부분은 해당하지 않습니다.** 생체역학 연구, 소규모 단면조사, 새 척도의 신뢰도 검증, 기존 결과의 반복 확인은 진료를 바꾸지 않습니다. 억지로 채우지 마십시오 — 한 편도 없으면 items를 비워 두고 summary에 그렇게 쓰십시오. 보통 한 주에 0~4편입니다.
3. 근거 강도를 정직하게 매기십시오. **강함**은 무작위시험이나 대규모 등록자료로 임상 결과를 본 경우입니다. **보통**은 잘 설계된 관찰연구나 체계적 문헌고찰입니다. **약함**은 표본이 작거나 대리지표만 본 경우로, 그래도 알아 둘 값이 있을 때만 넣으십시오.
4. `headline`은 **무엇이 달라지는지** 한 문장으로 씁니다. 논문 제목을 옮기지 마십시오. ("A가 B보다 낫다"가 아니라 "A를 쓰면 재원일이 하루 줄어든다"처럼 진료 언어로.)
5. `detail`은 근거의 핵심을 2~3문장으로. 표본 수, 비교 대상, 효과 크기를 숫자로 적으십시오.
6. `action`은 **당장 무엇을 할지** 한 문장으로. 바꿀 것이 없고 지켜보기만 할 상황이면 그렇게 쓰십시오.
7. `pmid`는 위 목록에 있는 번호만 씁니다. 목록에 없는 논문은 절대 인용하지 마십시오.
8. `summary`는 이번 주 전체를 한두 문장으로 요약합니다. 고를 것이 없었다면 무엇이 많았는지 적으십시오.
9. 모든 서술은 한국어로 작성하십시오."""


def recent_pmids(analysis: dict, days: int = WINDOW_DAYS) -> tuple[list[str], str, str]:
    """분석 창의 끝에서 days일 안에 실린 논문. 반환: (pmid 목록, 시작일, 종료일)."""
    date_to = analysis.get("dateTo") or date.today().isoformat()
    try:
        cutoff = (date.fromisoformat(date_to) - timedelta(days=days - 1)).isoformat()
    except ValueError:
        return [], "", date_to
    rows = [a for a in (analysis.get("articles") or []) if (a.get("date") or "") >= cutoff]
    rows.sort(key=lambda a: a.get("date") or "", reverse=True)
    return [a["pmid"] for a in rows[:MAX_ARTICLES]], cutoff, date_to


def _format(records) -> str:
    return "\n\n".join(
        f"[{i + 1}] PMID {r.pmid} · {r.journal} {r.year}\n제목: {r.title}\n초록: {r.abstract[:ABSTRACT_CHARS]}"
        for i, r in enumerate(records))


def build(pmids: list[str], period: str, credentials: NcbiCredentials,
          api_key: str, model: str) -> dict:
    """이번 주 초록을 읽고 진료에 영향 있는 것만 추린다. Gemini 호출 1회."""
    if not pmids:
        return {"error": "이번 주에 새로 실린 초록이 없습니다."}
    records = fetch_abstracts(pmids, credentials)
    if not records:
        return {"error": "PubMed에서 초록을 받아오지 못했습니다."}
    prompt = PROMPT.format(period=period, count=len(records), records=_format(records))
    parsed = call_gemini(api_key, model, prompt, BRIEFING_SCHEMA)
    allowed = {r.pmid: r for r in records}
    items, dropped = [], 0
    for raw in parsed.get("items") or []:
        if not isinstance(raw, dict):
            continue
        record = allowed.get(str(raw.get("pmid", "")))
        if record is None:
            # 목록에 없는 PMID는 버린다. 지어낸 인용을 화면에 올리지 않는다.
            dropped += 1
            continue
        items.append({
            "pmid": record.pmid, "title": record.title, "journal": record.journal,
            "headline": str(raw.get("headline", "")), "detail": str(raw.get("detail", "")),
            "area": raw.get("area") if raw.get("area") in AREAS else "",
            "strength": raw.get("strength") if raw.get("strength") in STRENGTHS else "",
            "action": str(raw.get("action", "")),
        })
    return {"model": model, "period": period, "reviewed": len(records),
            "summary": str(parsed.get("summary", "")), "items": items,
            "droppedCitations": dropped}


def week_key(stamp: str) -> str:
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
    return sorted((payload.get("weeks") or {}).keys(), reverse=True)


def record_week(briefing: dict, stamp: str, path: Path = HISTORY_PATH) -> str:
    """브리핑을 주차별로 남긴다. 실패한 브리핑은 남기지 않는다."""
    key = week_key(stamp)
    if not key or not isinstance(briefing, dict) or briefing.get("error"):
        return ""
    payload = load(path)
    payload["weeks"][key] = {**briefing, "at": stamp}
    for stale in sorted(payload["weeks"])[:-WEEKS_KEEP]:
        del payload["weeks"][stale]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", "utf-8")
    return key
