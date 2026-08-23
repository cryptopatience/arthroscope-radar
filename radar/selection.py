"""후보 평가 · 중복 제거 · 최종 조합 선택.

탐지기는 후보를 넉넉히 만든다. 여기서 다섯 개로 줄인다. 줄이는 방식이 중요하다.

점수 순으로 위에서 다섯 개를 자르면(greedy) 같은 종류가 목록을 채운다. 실제로
그렇게 됐다 — "감염 연구의 결과가 환자 체감 회복으로", "AI 연구의 결과가 환자 체감
회복으로", "로봇 연구의 결과가 환자 체감 회복으로"가 나란히 올라왔다. 주제 단어만
바뀌었을 뿐 연구 질문·설계·1차 결과변수의 구조가 같다.

그래서 두 단계를 둔다.
1. 의미상 중복 제거 — 같은 공백 틀에서 나온 것 중 가장 강한 하나만 남긴다
2. 전역 제약 아래 최고점 조합 선택 — 개별 점수 합이 아니라 "조합"을 고른다

조합 수는 자격을 통과한 후보 수에 따라 달라진다(14개에서 5개면 2,002가지,
15개면 3,003가지). 어느 쪽이든 부담이 없으므로 greedy 대신 완전 탐색을 쓰고,
실제로 훑은 수는 combinationsChecked로 남긴다.
"""
from __future__ import annotations

import itertools
import math
import re

from . import config
from .judge import BLOCKED_VERDICTS, evidence_summary
from .vocabulary import GAP_CATEGORIES, PROM_GAP_PENALTY

PROM_SUBTYPES = {"prom", "prom_interpretation"}
# 카테고리가 비었거나 사전에 없는 값이면 unknown으로 두고 최종 선정에서 제외한다.
# methodology 같은 그럴듯한 값으로 자동 대체하면 오분류가 조용히 최종 목록에 올라간다.
UNKNOWN = "unknown"


def gap_category(idea: dict) -> str:
    category = idea.get("gapCategory") or ""
    return category if category in GAP_CATEGORIES else UNKNOWN


def base_gap_key(idea: dict) -> str:
    """하위유형 접미사를 뗀 열쇠. 같은 클러스터의 같은 공백이면 같은 값이 된다."""
    gap = (idea.get("gapId") or "").split("@")[0]
    return f"{idea.get('clusterId', '')}:{gap}"


def source_gap_key(idea: dict) -> str:
    """같은 후보를 묶는 열쇠 — 분석 단위와 같은 `클러스터 × 공백`이다.

    gapId만으로 묶으면 안 된다. longterm_followup 하나로 묶으면

        외래·회복 × 장기 안전성
        비용·보건정책 × 장기 경제성
        형평성 × 장기 회복 격차

    가 한 덩어리가 되어 둘이 삭제된다. 서로 다른 연구 질문이므로 클러스터가 다르면
    다른 후보다. 같은 종류가 몰리는 것은 삭제가 아니라 카테고리 상한으로 막는다.
    """
    return f"{idea.get('clusterId', '')}:{idea.get('gapId', '')}"


_NON_WORD = re.compile(r"[^0-9a-z가-힣]+")


def semantic_key(idea: dict) -> str:
    """제목·PICO·1차 결과에서 **자기 클러스터 이름만** 지운 뒤 남는 문장 구조.

    지우는 대상을 자기 클러스터로 한정하는 것이 중요하다. 모든 주제 이름이나
    태그를 다 지우면 교차 공백의 두 번째 축까지 사라져

        전방십자인대·반월상 × 임플란트·기술
        전방십자인대·반월상 × 로봇·내비게이션
        전방십자인대·반월상 × 감염

    이 한 덩어리가 된다. 서로 다른 질문인데 둘이 삭제된다 — longterm_followup에서
    이미 한 번 저지른 실수와 같은 모양이다.

    자기 이름만 지우면, 클러스터 이름만 바꾼 같은 문장은 여전히 걸리고
    (1차 결과변수가 분야에 맞게 달라진 후보는 문장이 달라 살아남는다),
    두 번째 축은 그대로 남아 교차 공백끼리는 구분된다.
    """
    text = " ".join(str(idea.get(k, "")) for k in ("title", "pico", "primaryEndpoint"))
    own = idea.get("clusterId") or ""
    if own:
        text = text.replace(own, " ")
    return _NON_WORD.sub(" ", text.lower()).strip()


# ---------------------------------------------------------------------------
# 5축 평가
# ---------------------------------------------------------------------------

def _evidence_strength(idea: dict, evidence: dict | None) -> int:
    """실제 데이터에서 공백이 확인됐는가. 통계 지표에서 직접 나온다."""
    metrics = idea.get("metrics") or {}
    if not metrics.get("n"):
        return 0
    score = 0
    score += {"충분": 2, "제한적": 1}.get(metrics.get("sufficiency"), 0)
    score += 1 if abs(metrics.get("effectSize", 0)) >= config.GAP_H_SOLID else 0
    score += 1 if metrics.get("z", 0) >= 3.0 else 0
    score += 1 if (evidence or {}).get("level") == "높음" else 0
    return min(5, score)


def _novelty(idea: dict) -> int | None:
    """동일한 연구가 이미 수행되지 않았는가.

    최근 12개월 코퍼스로는 답할 수 없다. 선행연구 검증 결과가 없으면 None을 주고
    "아직 못 쟀다"고 표시한다 — 임의의 기본값을 넣으면 검증 안 한 후보가 검증한
    후보와 같은 점수를 받는다.
    """
    prior = idea.get("priorArt")
    if not isinstance(prior, dict) or prior.get("error"):
        return None
    hits = prior.get("matchCount")
    if hits is None:
        return None     # 검색 자체가 실패했거나 0건 — 높은 독창성이 아니라 미측정이다
    # 질문에 직접 답한 논문이 많을수록 독창성이 낮다. 일부 요소만 겹치는
    # adjacent는 감점하지 않되, 아무것도 없을 때 만점을 주지는 않는다.
    if hits == 0:
        return 4 if prior.get("adjacentCount", 0) else 5
    if hits <= 2:
        return 4
    if hits <= 5:
        return 3
    if hits <= 10:
        return 2
    return 1


def _clinical_importance(idea: dict, verdict: str) -> int:
    """결과가 진료 결정이나 환자 결과를 바꿀 수 있는가."""
    spec = idea.get("canonical") or {}
    score = {"opportunity": 3, "uncertain": 1}.get(verdict, 0)
    # 그 분야의 기준 결과변수와 맞닿은 공백일수록 중요하다.
    if idea.get("outcomeSubtype") in PROM_SUBTYPES and spec.get("prom_role") == "primary":
        score += 1
    if gap_category(idea) in ("clinical_utility_implementation", "comparator"):
        score += 1     # 진료 선택을 직접 바꾸는 종류
    if not spec.get("reviewed"):
        score -= 1     # 기준 결과변수가 잠정값이면 중요도 판단의 바탕이 약하다
    return max(0, min(5, score))


def _methodological_advance(idea: dict) -> int:
    """기존 연구보다 설계가 분명히 나아지는가."""
    by_category = {
        "methodology": 5,                    # 후향 → 전향은 근거 수준이 한 단계 오른다
        "comparator": 4,
        "population_external_validity": 4,   # 외부검증은 일반화 가능성을 직접 올린다
        "clinical_utility_implementation": 3,
        "longterm_durability": 3,
        "outcome_measurement": 2,
    }
    return by_category.get(gap_category(idea), 0)


def score_idea(idea: dict, judgment: dict | None) -> dict:
    """5축 점수와 합계. 못 잰 축은 None으로 남기고 합계에서 뺀다."""
    verdict = (judgment or {}).get("verdict", "")
    evidence = evidence_summary(judgment, idea.get("metrics")) if judgment else None
    axes = {
        "evidence_strength": _evidence_strength(idea, evidence),
        "novelty": _novelty(idea),
        "clinical_importance": _clinical_importance(idea, verdict),
        "methodological_advance": _methodological_advance(idea),
        "feasibility": max(0, min(5, int(idea.get("feasibility", 0)))),
    }
    measured = [v for v in axes.values() if v is not None]
    total = sum(measured) + (config.OPPORTUNITY_BONUS if verdict == "opportunity" else 0)
    # PROM이 1차 결과가 아닌 분야의 PROM 공백은 막지 않고 누른다. 생성 자체를 막으면
    # "감염이 치료된 환자의 기능 회복" 같은 정당한 질문까지 사라진다.
    penalty = 0
    if idea.get("outcomeSubtype") in PROM_SUBTYPES:
        penalty = PROM_GAP_PENALTY.get((idea.get("canonical") or {}).get("prom_role"), 0)
    total -= penalty
    return {"axes": axes, "total": total, "penalty": penalty,
            "unscored": [k for k, v in axes.items() if v is None],
            "verdict": verdict, "evidenceLevel": (evidence or {}).get("level")}


# ---------------------------------------------------------------------------
# 중복 제거
# ---------------------------------------------------------------------------

def dedupe(scored: list[tuple[dict, dict]]) -> list[tuple[dict, dict]]:
    """두 단계로 줄인다.

    1. 같은 `클러스터 × 공백`이 여러 번 나왔으면 가장 강한 하나만
    2. 클러스터 이름을 지웠을 때 문장 구조가 같은 것끼리 가장 강한 하나만

    2단계가 진짜 의미 중복을 잡는다. 1차 결과변수가 분야에 맞게 달라진 후보는
    이름을 지워도 문장이 달라 살아남고, 주제 단어만 바뀐 후보는 걸린다.
    """
    exact: dict[str, tuple[dict, dict]] = {}
    for idea, score in scored:
        key = source_gap_key(idea)
        if key not in exact or score["total"] > exact[key][1]["total"]:
            exact[key] = (idea, score)

    semantic: dict[str, tuple[dict, dict]] = {}
    for idea, score in sorted(exact.values(), key=lambda pair: -pair[1]["total"]):
        key = semantic_key(idea)
        if key not in semantic:
            semantic[key] = (idea, score)
    return sorted(semantic.values(), key=lambda pair: -pair[1]["total"])


# ---------------------------------------------------------------------------
# 전역 제약 조합 선택
# ---------------------------------------------------------------------------

def _violates(combo: list[tuple[dict, dict]]) -> bool:
    categories: dict[str, int] = {}
    prom = 0
    seen_gaps = set()
    shared_bases = set()
    for idea, _ in combo:
        category = gap_category(idea)
        if category == UNKNOWN:
            return True
        categories[category] = categories.get(category, 0) + 1
        if categories[category] > config.MAX_PER_GAP_CATEGORY:
            return True
        gap = source_gap_key(idea)
        if gap in seen_gaps:
            return True
        seen_gaps.add(gap)
        # 하위집단이 근거를 공유하면(형평성 접근성 ∩ 결과 격차) 두 아이디어의 점수를
        # 같은 논문이 동시에 밀어 올린다. 논문을 버리는 대신 여기서 하나만 고른다.
        if (idea.get("canonical") or {}).get("independentSubgroups") is False:
            base = base_gap_key(idea)
            if base in shared_bases:
                return True
            shared_bases.add(base)
        if idea.get("outcomeSubtype") in PROM_SUBTYPES:
            prom += 1
            # opportunity가 여럿 나와도 상한을 올리지 않는다. 대신 점수가 높은
            # PROM 후보 하나가 뽑히도록 둔다.
            if prom > config.MAX_PROM_IDEAS:
                return True
    return False


def _combo_value(combo: list[tuple[dict, dict]]) -> tuple[float, int]:
    total = sum(score["total"] for _, score in combo)
    distinct = len({gap_category(idea) for idea, _ in combo})
    # 카테고리 다양성은 동점을 가르는 데 쓴다. 점수를 직접 흔들면 약한 후보가
    # "종류가 다르다"는 이유만으로 강한 후보를 밀어낸다.
    return (total, distinct)


def eligible(idea: dict, score: dict) -> str:
    """최종 후보 자격. 통과하지 못한 이유를 문자열로 돌려준다(통과면 빈 문자열).

    최소 통과점수가 없으면 "5개 미만 허용"이 실제로는 작동하지 않는다. 후보가 전부
    양수 점수라 조합 최적화가 거의 항상 다섯 자리를 채우기 때문이다.
    """
    if gap_category(idea) == UNKNOWN:
        return "공백 유형 없음"
    if score["verdict"] in BLOCKED_VERDICTS:
        return "구조적 공백으로 판정"
    if not score["verdict"]:
        return "판정 없음"
    if "novelty" in score["unscored"] and not config.ALLOW_UNMEASURED_NOVELTY:
        return "독창성 미측정 (선행연구 검증 대기)"
    if score["total"] < config.MIN_FINAL_SCORE:
        return f"최소 통과점수 미달 ({score['total']} < {config.MIN_FINAL_SCORE})"
    return ""


def select(candidates: list[dict], judgments: dict, limit: int = 0) -> dict:
    """최종 아이디어 선택. 자격을 통과한 후보 중 제약을 만족하는 최고점 조합을 고른다."""
    limit = limit or config.FINAL_IDEAS
    scored: list[tuple[dict, dict]] = []
    blocked: list[tuple[dict, dict]] = []
    provisional: list[tuple[dict, dict]] = []
    for idea in candidates:
        judgment = judgments.get(idea["id"]) if isinstance(judgments, dict) else None
        judgment = judgment if isinstance(judgment, dict) and judgment.get("verdict") else None
        score = score_idea(idea, judgment)
        reason = eligible(idea, score)
        score["ineligible"] = reason
        if not reason:
            scored.append((idea, score))
        elif reason.startswith("독창성 미측정"):
            provisional.append((idea, score))
        else:
            blocked.append((idea, score))

    pool = dedupe(scored)
    dropped = [pair for pair in scored if pair not in pool]

    best: list[tuple[dict, dict]] = []
    best_value = (float("-inf"), 0)
    # 후보가 limit보다 적으면 그 개수로도 조합을 만든다. 근거가 부족하면 5개 미만을 허용한다.
    for size in range(min(limit, len(pool)), 0, -1):
        for combo in itertools.combinations(pool, size):
            if _violates(list(combo)):
                continue
            value = _combo_value(list(combo))
            if value > best_value:
                best_value, best = value, list(combo)
        if best:
            break   # 가장 큰 크기에서 답이 나오면 더 작은 조합은 볼 필요가 없다

    return {
        "final": [idea for idea, _ in best],
        "scores": {idea["id"]: score for idea, score in best},
        "allScores": {idea["id"]: score for idea, score in scored + blocked + provisional},
        "blocked": [idea for idea, _ in blocked],
        "provisional": [idea for idea, _ in provisional],
        "duplicates": [idea for idea, _ in dropped],
        "distinctCategories": best_value[1] if best else 0,
        "shortOfTarget": len(best) < limit,
        "combinationsChecked": _combinations_checked(len(pool), limit),
    }


def _combinations_checked(pool_size: int, limit: int) -> int:
    """실제로 훑은 조합 수. 후보 수에 따라 달라지므로 기록해 둔다."""
    size = min(limit, pool_size)
    return math.comb(pool_size, size) if size > 0 else 0
