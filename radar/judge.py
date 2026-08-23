"""공백 판정과 그 판정의 근거 수준.

이전에는 판정자가 스스로 매긴 `확신도 1~5`를 그대로 화면에 띄웠다. 실제로 돌려보니
15건이 전부 4점으로 나와 아무것도 구분하지 못했다. 모델이 자기 확신을 정량화하지
못한다는 것은 알려진 문제이므로, 주관적 점수를 버리고 밖에서 관측 가능한 네 가지로
바꾼다.

- 판정 안정성 : 같은 프롬프트를 여러 번 돌렸을 때 같은 판정이 나오는 비율
- 모델 간 합의 : 다른 모델(다른 회사 모델 포함)에게 물었을 때 같은 판정인지
- 시간적 근거 : 전반기·후반기로 나눠 다시 쟀을 때 공백이 실제로 움직였는지
- 표본 충분성 : 그 공백을 잰 문헌 수와 효과크기

앞 둘은 이 모듈이 호출로 만들고, 뒤 둘은 radar.analysis가 통계로 만든다.
"""
from __future__ import annotations

import collections
import json
import re
from dataclasses import dataclass

import requests

from . import config
from .gemini import GEMINI_DEFAULT_MODEL, call_gemini
from .vocabulary import (CANONICAL_OUTCOME_VERSION, GAP_CATEGORIES, KEYWORD_DICT_VERSION,
                         STRUCTURAL_PRIORS, canonical)

# 판정에 넣는 대표 논문 수. 제목만 보내므로 고도화보다 훨씬 싸다.
JUDGE_TITLES = 12
# 판정 프롬프트나 아래 스키마를 고치면 이 값을 올린다. 캐시 키에 들어가므로 옛 판정이 무효화된다.
# 프롬프트만 바꾸고 이 값을 안 올리면 지난 판정이 그대로 재사용된다.
JUDGE_PROMPT_VERSION = "5"
# 안정성 측정 반복 수. 홀수여야 동률이 덜 생긴다.
JUDGE_RUNS = 5
# 교차 검증용 두 번째 Gemini 모델. 같은 회사지만 크기·학습이 달라 판정이 실제로 갈린다.
GEMINI_SECOND_MODEL = "gemini-2.5-flash"
OPENAI_DEFAULT_MODEL = "gpt-4o"

# answered("이미 답해졌다")를 판정에서 뺐다. 그 질문은 최근 12개월 코퍼스로는
# 답할 수 없다 — 선행연구 검증 단계가 PubMed 전체를 다시 뒤져 판단한다.
# 대신 uncertain을 둔다: 근거가 얇거나 그 분야의 기준 결과변수 적합성이 애매한 경우.
VERDICTS = ("opportunity", "structural", "uncertain")
# 동률일 때 고르는 순서. 보수적으로 structural을 앞에 둔다.
TIE_ORDER = {"structural": 0, "uncertain": 1, "opportunity": 2}

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        # opportunity = 중요한데 실제로 비어 있다
        # structural  = 그 분야의 1차 결과가 원래 다르다. 통계적 공백일 뿐 기회가 아니다
        # uncertain   = 근거가 얇거나 기준 결과변수 적합성이 애매하다
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "reason": {"type": "string"},
        "fieldStandard": {"type": "string"},
    },
    "required": ["verdict", "reason", "fieldStandard"],
}


@dataclass
class Judge:
    provider: str   # "gemini" | "openai"
    model: str
    api_key: str

    @property
    def name(self) -> str:
        return self.model


# ---------------------------------------------------------------------------
# 호출
# ---------------------------------------------------------------------------

def _openai_schema(schema: dict) -> dict:
    """OpenAI structured outputs의 strict 모드 규칙에 맞춘다: 모든 키가 required, 추가 속성 금지."""
    return {**schema, "additionalProperties": False, "required": list(schema["properties"])}


def call_openai(api_key: str, model: str, prompt: str, schema: dict) -> dict:
    # temperature는 보내지 않는다. 일부 모델이 기본값 외의 값을 거부한다.
    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"authorization": f"Bearer {api_key}", "content-type": "application/json"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}],
              "response_format": {"type": "json_schema",
                                  "json_schema": {"name": "gap_verdict", "strict": True,
                                                  "schema": _openai_schema(schema)}}},
        timeout=180,
    )
    if not response.ok:
        m = re.search(r'"message"\s*:\s*"([^"]+)"', response.text)
        raise RuntimeError(f"OpenAI 호출 실패 ({m.group(1) if m else response.status_code})")
    payload = response.json()
    text = ((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    if not text:
        raise RuntimeError("OpenAI가 결과를 반환하지 않았습니다.")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise RuntimeError("OpenAI 응답을 해석하지 못했습니다.")


def cache_versions() -> str:
    """사전·기준표·임계값·프롬프트 버전을 한 문자열로. 캐시 키에 넣는다.

    하나라도 바뀌면 옛 판정이 재사용되면 안 된다. 프롬프트만 버전을 달아 두면
    용어 사전을 고쳤을 때 조용히 옛 결과가 살아남는다.
    """
    return (f"p{JUDGE_PROMPT_VERSION}.k{KEYWORD_DICT_VERSION}"
            f".c{CANONICAL_OUTCOME_VERSION}.t{config.CONFIG_VERSION}.r{JUDGE_RUNS}")


def build_panel(gemini_key: str, gemini_model: str = "", second_model: str = "",
                openai_key: str = "", openai_model: str = "") -> list[Judge]:
    """판정단. 첫 번째가 주 판정자이고, 안정성은 이 판정자를 반복해 잰다."""
    panel = [Judge("gemini", gemini_model or GEMINI_DEFAULT_MODEL, gemini_key)]
    second = second_model or GEMINI_SECOND_MODEL
    if second and second != panel[0].model:
        panel.append(Judge("gemini", second, gemini_key))
    if openai_key:
        panel.append(Judge("openai", openai_model or OPENAI_DEFAULT_MODEL, openai_key))
    return panel


def _ask(judge: Judge, prompt: str) -> dict:
    if judge.provider == "openai":
        parsed = call_openai(judge.api_key, judge.model, prompt, JUDGE_SCHEMA)
    else:
        parsed = call_gemini(judge.api_key, judge.model, prompt, JUDGE_SCHEMA)
    verdict = str(parsed.get("verdict", "")).lower()
    return {"verdict": verdict if verdict in VERDICTS else "uncertain",
            "reason": str(parsed.get("reason", "")),
            "fieldStandard": str(parsed.get("fieldStandard", ""))}


# ---------------------------------------------------------------------------
# 프롬프트
# ---------------------------------------------------------------------------

def judge_prompt(idea: dict, scope: str, period: str, titles: list[str]) -> str:
    cluster = idea.get("clusterId") or (idea.get("tags") or [""])[0]
    spec = idea.get("canonical") or canonical(cluster)
    category = idea.get("gapCategory", "")
    category_note = (GAP_CATEGORIES.get(category) or {}).get("note", "")
    structural_prior = STRUCTURAL_PRIORS.get(category, "")
    primary = ", ".join(spec.get("primary") or []) or "정의되지 않음"
    reviewed = "전문가 확인됨" if spec.get("reviewed") else "잠정값 (전문가 확인 전)"
    return f"""당신은 정형외과 무릎 분야의 연구 심사자입니다. 규칙 기반 분석기가 문헌 통계에서 공백을 하나 찾았습니다. 이 공백이 실제 연구 기회인지, 그 분야의 정상적 특성인지 판정하십시오.

[분석 범위] {scope} · {period}

[판정 대상 — 클러스터 하나가 아니라 "클러스터 × 공백" 하나입니다]
클러스터: {cluster}
공백 종류: {category} — {category_note}
제목: {idea.get('title', '')}
통계 근거: {idea.get('rationale', '')}

[이 분야가 원래 1차 결과로 삼는 것] ({reviewed})
{primary}

[이 종류의 공백에서 흔히 '구조적'인 경우]
{structural_prior}

[해당 주제의 대표 논문 제목]
{chr(10).join(f'- {t}' for t in titles)}

판정 기준:
1. 먼저 위 "이 종류의 공백에서 흔히 구조적인 경우"에 해당하는지 보십시오. 해당한다면 통계적으로 비어 있어도 연구 공백이 아니라 분야·연구단계의 정상적 특성이므로 **structural**입니다. 이 검사는 PROM뿐 아니라 모든 종류의 공백에 적용합니다 — 도입된 지 3년 된 기술에 10년 생존율이 없는 것, 단일군 feasibility 연구에 비교군이 없는 것, 발생률 1%인 합병증에 무작위시험이 없는 것은 전부 구조적입니다.
2. 다음으로 "이 분야가 원래 1차 결과로 삼는 것"과 이 공백이 묻는 지표를 견주십시오. 그 분야의 목적상 덜 보고되는 것이 자연스럽다면 역시 **structural**입니다. 다만 1차 결과가 아니라는 것과 연구 가치가 없다는 것은 다릅니다 — 예를 들어 감염이 치료된 뒤의 기능 회복은 1차 결과가 아니어도 물을 가치가 있습니다. 그 구분을 하십시오.
3. 위 기준 결과변수가 "잠정값"으로 표시돼 있고 그 정의가 이 분야에 맞지 않는다고 판단되면, 그 사실을 reason에 적고 **uncertain**으로 판정하십시오. 틀린 정의 위에서 내린 판정은 쓸모가 없습니다.
4. 통계 근거가 얇거나(표본이 작거나 격차가 미미하거나) 이 공백이 임상적으로 중요한지 판단이 서지 않으면 **uncertain**입니다. 억지로 둘 중 하나를 고르지 마십시오.
5. 그 분야의 목적에 비추어 중요한 결과인데도 실제로 비어 있고, 채우면 진료가 달라질 수 있다면 **opportunity**입니다.
6. 이 코퍼스를 3년치 추적한 결과, 이런 공백의 대부분은 해가 바뀌어도 유지되는 구조적 특성이었습니다(공백 크기의 연도간 상관 0.9 이상). 기본값은 structural이고 opportunity는 예외적인 판정입니다.
7. "이미 다른 학술지에서 답해졌는가"는 판정하지 마십시오. 이 프롬프트는 최근 {period} 코퍼스만 봅니다. 선행연구 확인은 다음 단계에서 PubMed 전체를 다시 검색해 따로 합니다.
8. fieldStandard에는 **판정과 무관하게 항상** 이 분야가 실제로 1차 결과로 쓰는 지표를 쓰십시오. structural이면 왜 공백이 아닌지의 근거가 되고, opportunity면 새 연구가 무엇과 비교돼야 하는지의 기준이 됩니다.
9. reason은 2~3문장의 한국어로, 임상적 근거를 들어 쓰십시오. 통계 수치를 반복하지 마십시오. 확신도를 스스로 매기지 마십시오 — 그 판단은 이 프롬프트를 여러 번 돌려 밖에서 잽니다."""


def titles_for_idea(idea: dict, pool: list[dict], limit: int = JUDGE_TITLES) -> list[str]:
    topics = [t for t in idea.get("tags", []) if any(t in a.get("topics", []) for a in pool)]
    matching = [a for a in pool if not topics or any(t in a["topics"] for t in topics)]
    return [a["title"] for a in sorted(matching, key=lambda a: a["date"], reverse=True)[:limit]]


# ---------------------------------------------------------------------------
# 판정
# ---------------------------------------------------------------------------

def judge_panel(idea: dict, titles: list[str], scope: str, period: str, panel: list[Judge],
                runs: int = JUDGE_RUNS) -> dict:
    """주 판정자를 runs회 반복해 안정성을 재고, 나머지 판정자에게 한 번씩 물어 합의를 잰다.

    반복은 프롬프트를 그대로 두고 돌린다. 온도를 올려 억지로 흔들면 "이 설정에서 이
    판정이 얼마나 재현되는가"라는 원래 질문에 답하지 못한다.
    """
    if not panel:
        raise RuntimeError("판정할 모델이 없습니다.")
    prompt = judge_prompt(idea, scope, period, titles[:JUDGE_TITLES])
    primary = panel[0]

    answers, failures = [], []
    for _ in range(max(1, runs)):
        try:
            answers.append(_ask(primary, prompt))
        except Exception as error:
            failures.append(str(error))
    if not answers:
        raise RuntimeError(failures[0] if failures else "판정 실패")

    counts = collections.Counter(a["verdict"] for a in answers)
    verdict = min(counts, key=lambda v: (-counts[v], TIE_ORDER.get(v, 3)))
    lead = next(a for a in answers if a["verdict"] == verdict)

    others = []
    for judge in panel[1:]:
        try:
            answer = _ask(judge, prompt)
            others.append({"model": judge.name, "provider": judge.provider, "verdict": answer["verdict"]})
        except Exception as error:
            others.append({"model": judge.name, "provider": judge.provider, "error": str(error)})

    voted = [o for o in others if o.get("verdict")]
    consensus = ("single" if not voted else
                 "agree" if all(o["verdict"] == verdict for o in voted) else "split")
    return {
        "model": primary.name,
        "verdict": verdict,
        "reason": lead["reason"],
        "fieldStandard": lead["fieldStandard"],
        "stability": {"runs": len(answers), "agree": counts[verdict],
                      "rate": round(counts[verdict] / len(answers), 2),
                      "verdicts": dict(counts), "failed": len(failures)},
        "consensus": consensus,
        "panel": others,
    }


# ---------------------------------------------------------------------------
# 근거 수준
# ---------------------------------------------------------------------------

VERDICT_LABEL = {
    "opportunity": "추천 연구기회",
    "structural": "구조적 공백 — 이 분야의 정상적 특성",
    "uncertain": "검토 필요 — 근거 또는 적합성이 애매함",
}

# structural은 최종 후보에서 제외한다. uncertain은 남기되 가점을 주지 않는다.
BLOCKED_VERDICTS = {"structural"}

# 판정과 방향이 맞는 시간 변화. structural이면 공백이 그대로 유지돼야 판정이 맞고,
# opportunity·answered면 최근 반기에 실제로 움직였어야 판정이 맞다.
# uncertain은 어느 쪽도 예측하지 않으므로 시간 신호로 지지·반박되지 않는다.
TEMPORAL_SUPPORT = {"opportunity": {"narrowing"},
                    "structural": {"persistent", "widening"},
                    "uncertain": set()}
TEMPORAL_TEXT = {"narrowing": "공백이 좁혀지는 중", "widening": "공백이 벌어지는 중",
                 "persistent": "공백이 그대로 유지됨", "unknown": "반기 표본이 적어 판단 불가"}


def evidence_summary(judgment: dict | None, metrics: dict | None) -> dict | None:
    """네 신호를 합쳐 종합 근거 수준을 낸다. 없는 신호는 0점으로 두고 그렇다고 표시한다.

    점수는 -4 ~ +5. 각 신호가 판정을 지지하면 더하고 반박하면 뺀다. 임의의 가중치를
    피하려고 안정성만 2점을 주는데, 나머지 셋과 달리 판정 자체를 직접 재현한 값이라서다.
    """
    judgment = judgment or {}
    metrics = metrics or {}
    verdict = judgment.get("verdict")
    if verdict not in VERDICTS:
        return None

    score, measured = 0, 0
    stability = judgment.get("stability") or {}
    if stability.get("runs"):
        measured += 1
        rate = stability.get("rate", 0)
        # 만점은 성공한 반복이 3회 이상일 때만 준다. 5회 중 3회가 호출 실패로 날아가고
        # 남은 2회가 일치한 것을 "완전히 재현됐다"고 부를 수는 없다.
        full = rate >= 1.0 and stability["runs"] >= 3
        score += 2 if full else 1 if rate >= 0.8 else 0 if rate >= 0.6 else -1
        stability_text = f"{stability['runs']}회 중 {stability.get('agree', 0)}회"
        if stability.get("failed"):
            stability_text += f" (호출 실패 {stability['failed']}회)"
    else:
        stability_text = "측정 안 됨 (1회 판정)"

    # 저장된 consensus 문자열을 믿지 않고 실제 표에서 다시 센다. 화면에 "불일치"라고
    # 써 놓고 바로 옆에 같은 판정을 나열하는 모순이 생기지 않는다.
    voted = [p for p in (judgment.get("panel") or []) if p.get("verdict")]
    consensus = ("single" if not voted else
                 "agree" if all(p["verdict"] == verdict for p in voted) else "split")
    if consensus == "agree":
        measured += 1
        score += 1
        consensus_text = "일치 — " + ", ".join(p["model"] for p in voted)
    elif consensus == "split":
        measured += 1
        score -= 1
        consensus_text = "불일치 — " + ", ".join(f"{p['model']}: {VERDICT_LABEL[p['verdict']].split(' —')[0]}"
                                                for p in voted)
    else:
        consensus_text = "단일 판정자 (교차 검증 없음)"

    temporal = (metrics.get("temporal") or {}).get("direction", "unknown")
    if temporal != "unknown" and verdict == "uncertain":
        # 판정이 아직 어느 쪽도 아니면 시간 신호는 지지도 반박도 아니다. 보여만 준다.
        temporal_text = f"참고 — {TEMPORAL_TEXT[temporal]}"
    elif temporal != "unknown":
        measured += 1
        supports = temporal in TEMPORAL_SUPPORT.get(verdict, set())
        score += 1 if supports else -1
        temporal_text = ("있음" if supports else "반대 방향") + f" — {TEMPORAL_TEXT[temporal]}"
    else:
        temporal_text = TEMPORAL_TEXT["unknown"]

    sufficiency = metrics.get("sufficiency")
    score += {"충분": 1, "제한적": 0, "부족": -1}.get(sufficiency, 0)
    if metrics.get("n"):
        measured += 1
        sample_text = f"관련 문헌 {metrics['n']}편 · 효과크기 {metrics.get('effectSize', 0):.2f} ({sufficiency})"
    else:
        sample_text = "측정 안 됨"

    # 아무것도 재지 못했으면 점수 0이 나오는데, 이때 "낮음"이라고 쓰면 근거가 약하다는
    # 뜻으로 읽힌다. 실제로는 아직 재지 않았을 뿐이라 그대로 말한다(옛 스냅샷 등).
    level = ("미측정" if not measured else
             "높음" if score >= 3 else "중간" if score >= 1 else "낮음")
    return {"level": level, "score": score, "stability": stability_text, "consensus": consensus_text,
            "temporal": temporal_text, "sample": sample_text}
