"""Gemini 호출 (lib/gemini.ts + api/enhance 포팅)."""
from __future__ import annotations

import json
import re

import requests

from .ncbi import NcbiCredentials, PubmedRecord, fetch_abstracts

GEMINI_DEFAULT_MODEL = "gemini-2.5-pro"
ABSTRACT_CHARS = 1400

# 고도화 한 번에 보내는 초록 수. 너무 적으면 추론할 근거가 없고, 많으면 비용이 커진다.
ENHANCE_MIN = 20
ENHANCE_MAX = 36

# 판정 단계에 넣는 대표 논문 수. 제목만 보내므로 고도화보다 훨씬 싸다.
JUDGE_TITLES = 12
# 판정 프롬프트를 고치면 이 값을 올린다. 캐시 키에 들어가므로 옛 판정이 무효화된다.
# 프롬프트만 바꾸고 이 값을 안 올리면 지난 판정이 그대로 재사용된다.
JUDGE_PROMPT_VERSION = "2"

IDEA_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {"type": "string"},
        "pico": {"type": "object",
                 "properties": {"population": {"type": "string"}, "intervention": {"type": "string"},
                                "comparison": {"type": "string"}, "outcome": {"type": "string"}},
                 "required": ["population", "intervention", "comparison", "outcome"]},
        "gap": {"type": "string"},
        "design": {"type": "string"},
        "limitations": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "items": {"type": "object",
                                                "properties": {"pmid": {"type": "string"}, "note": {"type": "string"}},
                                                "required": ["pmid", "note"]}},
    },
    "required": ["question", "pico", "gap", "design", "limitations", "evidence"],
}

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        # opportunity  = 실제로 비어 있고 채울 가치가 있다
        # structural   = 그 분야의 1차 결과가 원래 다르다. 통계적 공백일 뿐 기회가 아니다
        # answered     = 이미 충분히 답해졌거나 이 코퍼스 밖에서 다뤄지고 있다
        "verdict": {"type": "string", "enum": ["opportunity", "structural", "answered"]},
        "reason": {"type": "string"},
        "confidence": {"type": "integer"},
        "fieldStandard": {"type": "string"},
    },
    "required": ["verdict", "reason", "confidence"],
}

TREND_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "movements": {"type": "array", "items": {"type": "object",
                                                 "properties": {"topic": {"type": "string"}, "reading": {"type": "string"},
                                                                "evidence": {"type": "array", "items": {"type": "string"}}},
                                                 "required": ["topic", "reading", "evidence"]}},
        "watchList": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "summary", "movements", "watchList"],
}


def call_gemini(api_key: str, model: str, prompt: str, schema: dict) -> dict:
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"content-type": "application/json", "x-goog-api-key": api_key},
        json={"contents": [{"role": "user", "parts": [{"text": prompt}]}],
              "generationConfig": {"temperature": 0.4, "responseMimeType": "application/json", "responseSchema": schema}},
        timeout=180,
    )
    if not response.ok:
        m = re.search(r'"message"\s*:\s*"([^"]+)"', response.text)
        raise RuntimeError(f"Gemini 호출 실패 ({m.group(1) if m else response.status_code})")
    payload = response.json()
    candidates = payload.get("candidates") or [{}]
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts)
    if not text:
        raise RuntimeError(f"Gemini가 결과를 반환하지 않았습니다 ({candidates[0].get('finishReason', '빈 응답')})")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise RuntimeError("Gemini 응답을 해석하지 못했습니다.")


def _format_trends(trends: list[dict]) -> str:
    return "\n".join(
        f"- {t['label']}: {t['count']}편 (비중 {t['share']}%), 전반기 대비 {'+' if t['delta'] > 0 else ''}{t['delta']}%, 판정 {t['signal']}"
        for t in trends[:14])


def _format_records(records: list[PubmedRecord]) -> str:
    return "\n\n".join(
        f"[{i + 1}] PMID {r.pmid} · {r.journal} {r.year}\n제목: {r.title}\n초록: {r.abstract[:ABSTRACT_CHARS]}"
        for i, r in enumerate(records))


def idea_prompt(idea: dict, trends: list[dict], scope: str, period: str, records: list[PubmedRecord]) -> str:
    return f"""당신은 정형외과 무릎 분야의 연구방법론 심사자입니다. 아래는 규칙 기반 분석기가 문헌 통계에서 찾아낸 연구 아이디어 후보와, 그 근거가 된 실제 초록들입니다. 이 후보를 연구계획서로 발전시킬 수 있도록 구체화하십시오.

[분석 범위] {scope} · {period}

[규칙 기반 후보]
제목: {idea.get('title', '')}
근거: {idea.get('rationale', '')}
초안 PICO: {idea.get('pico', '')}
초안 설계: {idea.get('design', '')}
초안 1차 결과: {idea.get('primaryEndpoint', '')}
주제 태그: {', '.join(idea.get('tags', []))}

[해당 범위의 주제별 통계]
{_format_trends(trends)}

[관련 초록 {len(records)}편]
{_format_records(records)}

지시사항:
1. 제시된 초록에 실제로 담긴 내용만 근거로 삼으십시오. 초록에 없는 수치나 결과를 지어내지 마십시오.
2. 선행연구 공백은 위 초록들이 무엇을 이미 다뤘고 무엇을 다루지 않았는지에 근거해 서술하십시오. 통계만 반복하지 마십시오.
3. PICO는 실제로 측정 가능한 변수로 쓰십시오. 대조군이 불명확하면 무엇을 대조로 삼아야 하는지 명시하십시오.
4. 예상 한계는 이 설계에서 실제로 발생할 교란·편향·탈락을 구체적으로 쓰십시오. 일반론은 쓰지 마십시오.
5. 근거 PMID는 위 목록에 있는 것만 인용하고, 각 논문이 이 연구 질문과 어떻게 연결되는지 한 문장으로 쓰십시오.
6. 이 질문이 이미 충분히 답해졌다고 판단되면, 공백 항목에 그렇게 쓰고 그 근거 PMID를 제시하십시오.
7. 모든 서술은 한국어로 작성하십시오."""


def trend_prompt(family: str, period: str, analyzed: int, trends: list[dict], records: list[PubmedRecord]) -> str:
    return f"""당신은 정형외과 무릎 분야의 문헌 동향 분석가입니다. 아래는 최근 {period} 동안 {family}에서 수집한 무릎 관련 초록 {analyzed}편의 주제별 통계와, 그중 최근 초록들입니다.

[주제별 통계]
{_format_trends(trends)}

[최근 초록 {len(records)}편]
{_format_records(records)}

지시사항:
1. 통계 수치를 그대로 옮겨 적지 말고, 그 변화가 임상적으로 무엇을 뜻하는지 해석하십시오.
2. 편수가 적은 주제의 큰 변화율은 신뢰하지 마십시오. 표본이 작으면 그렇게 명시하십시오.
3. 각 movement의 evidence에는 위 초록 목록에 있는 PMID만 넣으십시오.
4. watchList에는 아직 신호로 잡히지 않았지만 초록에서 관찰되는 초기 움직임을 쓰십시오. 없으면 빈 배열로 두십시오.
5. headline은 한 문장, summary는 3~4문장으로 쓰십시오.
6. 모든 서술은 한국어로 작성하십시오."""


def judge_prompt(idea: dict, scope: str, period: str, titles: list[str]) -> str:
    return f"""당신은 정형외과 무릎 분야의 연구 심사자입니다. 규칙 기반 분석기가 문헌 통계에서 "공백"을 하나 찾았습니다. 이 공백이 실제 연구 기회인지, 아니면 그 분야의 정상적인 특성인지 판정하십시오.

[분석 범위] {scope} · {period}

[통계가 찾아낸 공백]
제목: {idea.get('title', '')}
근거: {idea.get('rationale', '')}
주제 태그: {', '.join(idea.get('tags', []))}

[해당 주제의 대표 논문 제목]
{chr(10).join(f'- {t}' for t in titles)}

판정 기준:
1. 이 지표가 낮은 것이 그 분야에서 **당연한** 일인지 먼저 따지십시오. 예를 들어 인공관절 감염 연구의 1차 결과는 균 박멸·재감염이지 환자보고 결과가 아닙니다. 이런 경우는 통계적으로 비어 있어도 연구 공백이 아니라 분야의 정상적 특성이므로 structural입니다. 이때 fieldStandard에 그 분야가 실제로 쓰는 1차 결과를 쓰십시오.
2. 이 질문이 이미 충분히 답해졌거나, 이 코퍼스에 포함되지 않은 다른 학술지·학회에서 활발히 다뤄지고 있다고 판단되면 answered입니다.
3. 위 둘 다 아니고, 임상적으로 답이 필요한데 실제로 비어 있다면 opportunity입니다.
4. 이 코퍼스를 3년치 추적한 결과, 이런 공백의 대부분은 해가 바뀌어도 그대로 유지되는 구조적 특성이었습니다(공백 크기의 연도간 상관 0.9 이상). 따라서 기본값은 structural이고, opportunity는 예외적인 판정입니다. 애매하면 structural로 판정하십시오.
5. confidence는 1~5 정수입니다. 5는 이 분야를 아는 사람이라면 누구나 동의할 명백한 경우에만 쓰십시오. 판단이 갈릴 수 있으면 3, 근거가 약하면 2 이하로 쓰십시오. 대부분의 판정은 3~4에 놓여야 정상입니다.
6. reason은 2~3문장의 한국어로, 왜 그렇게 판정했는지 임상적 근거를 들어 쓰십시오. 통계 수치를 반복하지 마십시오."""


def judge_gap(idea: dict, titles: list[str], scope: str, period: str,
              api_key: str, model: str = GEMINI_DEFAULT_MODEL) -> dict:
    """공백이 실제 기회인지 분야 특성인지 판정한다. 고도화 전에 거르는 용도."""
    parsed = call_gemini(api_key, model, judge_prompt(idea, scope, period, titles[:JUDGE_TITLES]), JUDGE_SCHEMA)
    verdict = str(parsed.get("verdict", "")).lower()
    if verdict not in ("opportunity", "structural", "answered"):
        verdict = "answered"
    try:
        confidence = max(1, min(5, int(parsed.get("confidence", 3))))
    except (TypeError, ValueError):
        confidence = 3
    return {"model": model, "verdict": verdict, "confidence": confidence,
            "reason": str(parsed.get("reason", "")), "fieldStandard": str(parsed.get("fieldStandard", ""))}


def titles_for_idea(idea: dict, pool: list[dict], limit: int = JUDGE_TITLES) -> list[str]:
    topics = [t for t in idea.get("tags", []) if any(t in a.get("topics", []) for a in pool)]
    matching = [a for a in pool if not topics or any(t in a["topics"] for t in topics)]
    return [a["title"] for a in sorted(matching, key=lambda a: a["date"], reverse=True)[:limit]]


def pmids_for_idea(idea: dict, pool: list[dict], trends: list[dict], limit: int = ENHANCE_MAX) -> list[str]:
    """아이디어 뒤의 초록: 자체 근거 먼저, 그 다음 같은 주제의 최신 논문."""
    topics = [t for t in idea.get("tags", []) if any(tr["label"] == t for tr in trends)]
    seeds = [e["pmid"] for e in idea.get("evidence", [])]
    related = sorted((a for a in pool if not topics or any(t in a["topics"] for t in topics)),
                     key=lambda a: a["date"], reverse=True)
    seen, out = set(), []
    for pmid in seeds + [a["pmid"] for a in related]:
        if pmid not in seen:
            seen.add(pmid)
            out.append(pmid)
    return out[:limit]


def _keep_known(raw, records: list[PubmedRecord]) -> tuple[list[dict], int]:
    allowed = {r.pmid for r in records}
    items = raw if isinstance(raw, list) else []
    kept = [i for i in items if isinstance(i, dict) and i.get("pmid") in allowed]
    return kept, len(items) - len(kept)


def enhance_idea(idea: dict, pmids: list[str], trends: list[dict], scope: str, period: str,
                 credentials: NcbiCredentials, api_key: str, model: str = GEMINI_DEFAULT_MODEL) -> dict:
    """규칙 기반 아이디어를 Gemini가 근거 초록에 맞춰 구체화한다. 인용은 보낸 PMID로만 제한."""
    pmids = list(dict.fromkeys(p for p in pmids if p.isdigit()))[:ENHANCE_MAX]
    if len(pmids) < ENHANCE_MIN:
        raise RuntimeError(f"근거로 쓸 초록이 {len(pmids)}편뿐입니다. {ENHANCE_MIN}편 이상이어야 고도화할 수 있습니다.")
    records = fetch_abstracts(pmids, credentials)
    if len(records) < ENHANCE_MIN:
        raise RuntimeError(f"PubMed에서 초록 {len(records)}편만 받아왔습니다. 잠시 후 다시 시도해 주세요.")
    parsed = call_gemini(api_key, model, idea_prompt(idea, trends, scope, period, records), IDEA_SCHEMA)
    kept, dropped = _keep_known(parsed.get("evidence"), records)
    return {
        "model": model,
        "abstractsUsed": len(records),
        "question": str(parsed.get("question", "")),
        "pico": parsed.get("pico") or None,
        "gap": str(parsed.get("gap", "")),
        "design": str(parsed.get("design", "")),
        "limitations": [str(x) for x in parsed.get("limitations", [])] if isinstance(parsed.get("limitations"), list) else [],
        "evidence": kept,
        "droppedCitations": dropped,
    }


def family_trend_report(family_label: str, period: str, pool: list[dict], trends: list[dict],
                        credentials: NcbiCredentials, api_key: str, model: str, abstracts: int = 30) -> dict:
    if not pool or not trends:
        return {"error": "이 계열에 분석할 문헌이 없습니다."}
    records = fetch_abstracts([a["pmid"] for a in pool[:abstracts]], credentials)
    parsed = call_gemini(api_key, model, trend_prompt(family_label, period, len(pool), trends, records), TREND_SCHEMA)
    allowed = {r.pmid for r in records}
    movements = []
    for raw in parsed.get("movements") or []:
        if not isinstance(raw, dict):
            continue
        evidence = raw.get("evidence") if isinstance(raw.get("evidence"), list) else []
        movements.append({"topic": str(raw.get("topic", "")), "reading": str(raw.get("reading", "")),
                          "evidence": [str(p) for p in evidence if str(p) in allowed]})
    return {
        "model": model, "abstractsUsed": len(records),
        "headline": str(parsed.get("headline", "")), "summary": str(parsed.get("summary", "")),
        "movements": movements,
        "watchList": [str(x) for x in parsed.get("watchList", [])] if isinstance(parsed.get("watchList"), list) else [],
    }
