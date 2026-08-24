"""탐지에 쓰는 어휘와 분야별 기준 결과변수.

analysis.py에서 분리한 이유는 두 가지다.

1. 이 사전을 고치면 과거 판정이 무효가 된다. 버전을 달아 캐시 키에 넣어야 한다.
2. 여기 있는 값은 통계가 아니라 임상 지식이다. 코드와 섞여 있으면 전문가가 고치기 어렵다.

사전을 고칠 때는 반드시 아래 버전을 올린다. 올리지 않으면 옛 판정이 그대로 재사용된다.
"""
from __future__ import annotations

# 용어 사전을 고치면 올린다.
KEYWORD_DICT_VERSION = "2"
# 분야별 기준 결과변수(CANONICAL_OUTCOMES)를 고치면 올린다.
CANONICAL_OUTCOME_VERSION = "7"


# ---------------------------------------------------------------------------
# PROM 3층
#
# 세 층은 묻는 것이 다르다. 하나로 뭉치면 "PROM을 쟀다"와 "그 변화가 환자에게
# 의미 있는지 해석했다"가 구분되지 않아, 이미 PROM을 충분히 보고하는 분야에서
# 해석 공백을 못 찾는다.
# ---------------------------------------------------------------------------

# 층위 1 — PROM을 실제로 수집·보고했는가
PROM_INSTRUMENTS = [
    "patient-reported", "patient reported", "prom", "proms", "promis",
    "koos", "koos-jr", "womac", "oxford knee", "oks", "forgotten joint", "fjs",
    "ikdc", "lysholm", "tegner", "kujala", "hoos", "marx activity", "knee society score",
    "eq-5d", "euroqol", "sf-36", "sf-12", "vr-12", "veterans rand",
    "visual analog*", "visual analogue*", "numeric rating scale",
    "satisfaction", "expectation",
]

# 층위 2 — 점수 변화가 환자에게 의미 있는지 해석했는가
# MDC/SEM은 여기 넣지 않는다. 측정오차는 "의미 있는 변화"가 아니라 "잴 수 있는 변화"다.
PROM_CLINICAL_INTERPRETATION = [
    "minimal clinically important", "minimally clinically important", "mcid",
    "minimal important change", "minimally important difference", "mic", "mid",
    "substantial clinical benefit", "scb",
    "patient acceptable symptom", "pass",
    "responder analysis", "responder rate", "treatment failure threshold",
    "clinically meaningful improvement", "clinically significant improvement",
    "anchor-based", "distribution-based",
]

# 층위 3 — 관찰된 변화가 측정오차보다 큰가
PROM_MEASUREMENT_ERROR = [
    "minimal detectable change", "mdc", "smallest detectable change", "sdc",
    "standard error of measurement", "measurement error",
    "test-retest", "test retest", "reliability", "intraclass correlation", "icc",
    "responsiveness", "floor effect", "ceiling effect",
]


# ---------------------------------------------------------------------------
# 공백 카테고리 6종
#
# technology(로봇·AI)는 연구 대상이지 공백의 종류가 아니다. 로봇 연구의 공백도
# 기존 술기와 직접 비교가 없으면 comparator, 정확도가 환자 결과로 이어지는지
# 모르면 clinical_utility_implementation, 장기 자료가 없으면 longterm_durability다.
# ---------------------------------------------------------------------------

GAP_CATEGORIES = {
    "outcome_measurement": {
        "label": "결과 측정·해석",
        "note": "결과를 재고 해석하는 방식이 비어 있다 (MCID·PASS·표준화 지표)",
    },
    "methodology": {
        "label": "연구설계",
        "note": "후향·단일기관·소표본에 머물러 설계 자체가 결론을 제한한다",
    },
    "comparator": {
        "label": "직접 비교",
        "note": "임상에서 실제로 갈리는 선택지끼리의 직접 비교가 없다",
    },
    "population_external_validity": {
        "label": "대상군·외부검증",
        "note": "특정 환자군에서 따로 검증되지 않았거나 외부 코호트 검증이 없다",
    },
    "clinical_utility_implementation": {
        "label": "임상적 유용성",
        "note": "지표는 좋아졌는데 그것이 진료 결정이나 환자 결과를 바꾸는지 모른다",
    },
    "longterm_durability": {
        "label": "장기 결과",
        "note": "단기 결과만 있고 장기 생존·재발 자료가 없다",
    },
}
GAP_CATEGORY_ORDER = list(GAP_CATEGORIES)


# ---------------------------------------------------------------------------
# 분야별 기준 결과변수
#
# 그 분야가 원래 무엇을 1차 결과로 삼는지를 먼저 적어 둔다. 이것이 없으면
# "PJI 연구에 PROM이 적다"를 공백으로 착각한다 — 그 분야의 1차 결과는 감염
# 박멸이지 환자 체감 점수가 아니다.
#
# prom_role — "PROM이 적다"를 어떻게 다룰지 정한다.
#   primary        PROM이 그 분야의 1차 결과다. 공백이면 그대로 후보로 올린다
#   contextual     맥락에 따라 다르다. 근거가 강할 때만 올린다
#   secondary      1차 결과는 아니다. 후보로 올리되 감점하고 판정을 반드시 받는다
#   not_applicable 이 분야에서 PROM은 애초에 결과변수가 아니다. 생성 자체를 막는다
#
# secondary를 hard block하지 않는 이유: "중요하지 않다"가 아니라 "1차가 아니다"라는
# 뜻이기 때문이다. PJI에서 감염이 치료된 환자의 기능 회복은 여전히 연구 가치가 있다.
# 통계로는 그 구분을 못 하므로, 막지 말고 판정에 맡기고 점수로 눌러야 한다.
#
# reviewed=False는 전문가 확인을 아직 받지 않은 잠정값이라는 뜻이다.
# 화면과 보고서에 그렇게 표시하고, 판정에서도 보수적으로 다룬다.
# 2026-08-23 기준 16개 전부 확인 완료. 새 주제를 추가할 때는 False로 두고 시작한다.
# ---------------------------------------------------------------------------

CANONICAL_OUTCOMES = {
    "감염": {
        "primary": ["감염 박멸", "재감염률", "임플란트 생존"],
        # 재감염은 대부분 2년 안에 드러난다. 10년을 기다릴 질문이 아니다.
        "horizon": "2–5년",
        "prom_role": "secondary", "reviewed": True,
    },
    "재치환·전환 수술": {
        "primary": ["재치환 생존", "합병증", "기능 회복", "PROM"],
        "horizon": "5–10년",
        "prom_role": "primary", "reviewed": True,
    },
    "로봇·내비게이션": {
        "primary": ["정렬 정확도", "outlier 비율", "합병증", "수술 효율"],
        # 정확도·수술 효율은 한 시점 지표라 장기 질문의 결과변수가 될 수 없다.
        "longterm": ["임플란트 생존", "누적 재수술", "후기 이완·migration"],
        "horizon": "5–10년",
        "prom_role": "contextual", "reviewed": True,
    },
    "정렬·생체역학": {
        "primary": ["정렬 정확도", "outlier 비율", "합병증", "수술 효율"],
        "longterm": ["임플란트 생존", "누적 재수술", "후기 이완·migration"],
        "horizon": "5–10년",
        "prom_role": "contextual", "reviewed": True,
    },
    "AI·예측": {
        "primary": ["discrimination (AUC)", "calibration", "external validation", "clinical utility"],
        # 모델 성능 지표는 시점 지표다. 장기 질문은 성능이 시간에 따라 유지되는지를 묻는다.
        # 예측모델의 장기 평가는 모델이 예측하려는 기간에 따라 달라진다.
        # "유지"는 같은 모델을 여러 시기에 반복 평가해 drift를 본 경우에만 쓴다.
        # 한 시점의 장기 예측 성능은 "5년 예측모델의 5년 시점 calibration"이다.
        "longterm": ["5년 예측모델의 5년 시점 calibration", "5년 시점 discrimination"],
        "horizon": "모델의 예측기간(예: 5년)",
        "prom_role": "contextual", "reviewed": True,
    },
    "형평성": {
        # 형평성은 하나가 아니다. 접근성 연구와 수술 후 결과 격차 연구는 1차 결과도
        # PROM의 위치도 다르다. 전체를 primary로 두면 접근성 연구에 PROM이 없는 것을
        # 다시 공백으로 오판한다. 기본값은 보수적으로 두고 variants로 가른다.
        "primary": ["치료 접근성", "치료 격차", "회복 격차"],
        "horizon": "5년",
        "prom_role": "contextual", "reviewed": True,
        "variants": {
            "access": {
                "label": "형평성–의료 접근성",
                "terms": ["utilization", "underutilization", "access to care", "referral",
                          "wait time", "waiting time", "insurance", "medicaid", "uninsured",
                          "coverage", "eligibility", "geographic", "rural", "travel distance"],
                "primary": ["수술 이용률", "의뢰율", "대기시간", "보험·지역 격차"],
                # 접근성 연구의 1차 결과는 "받았는가"이지 "얼마나 좋아졌는가"가 아니다.
                "longterm": ["이용률 격차의 추세", "보험·지역 격차의 지속"],
                "longtermDesign": "반복 단면 또는 인구집단 연구 (격차 추세 분석)",
                # 접근성은 인구집단 단위 질문이라 "수술 후 추적"이라는 말이 성립하지 않는다.
                "populationLevel": True,
                "prom_role": "contextual",
            },
            "outcome_gap": {
                "label": "형평성–수술 후 결과 격차",
                "terms": ["outcome disparit", "differential improvement", "recovery gap",
                          "worse outcomes", "poorer outcomes", "postoperative disparit",
                          "koos", "womac", "oxford knee", "patient-reported"],
                "primary": ["집단 간 PROM·기능 회복 궤적", "합병증 격차", "재수술 격차"],
                "longterm": ["집단 간 회복 격차 (PROM·기능 궤적 차이)"],
                "longtermDesign": "수술받은 환자의 종단 코호트 (집단 간 회복 궤적 비교)",
                "prom_role": "primary",
            },
        },
    },
    "환자요인": {
        "primary": ["위험도", "회복", "합병증", "PROM"],
        "horizon": "2–5년",
        "prom_role": "contextual", "reviewed": True,
    },
    "재수술·합병증": {
        # 클러스터가 이차적이라는 뜻이 아니라, 이 분야에서 PROM이 이차적이라는 뜻이다.
        "primary": ["재수술·재치환 발생률", "revision-free survival", "합병증률",
                    "원인별 실패", "implant survival"],
        "horizon": "5–10년",
        "prom_role": "secondary", "reviewed": True,
    },
    "PROM·기대치": {
        "primary": ["PROM 변화", "기대-결과 불일치", "기대 충족률", "만족도",
                    "MCID/SCB/PASS 달성률"],
        "longterm": ["PROM 개선의 장기 유지(회복 궤적)", "만족도 유지"],
        "horizon": "2–5년",
        "prom_role": "primary", "reviewed": True,
    },
    "외래·회복": {
        "primary": ["당일 퇴원 성공·실패", "재원기간", "응급실 방문",
                    "30/90일 재입원·합병증", "기능회복 시간"],
        # 재원기간·당일 퇴원은 수술 당시 한 번 재는 지표다. 5년 질문에는 누적 지표를 쓴다.
        "longterm": ["누적 재입원·재수술·합병증", "PROM 개선의 장기 유지(회복 궤적)"],
        # 외래 수술의 장기 질문은 10년이 아니라 2~5년이다.
        "horizon": "2–5년",
        "prom_role": "secondary", "reviewed": True,
    },
    "비용·보건정책": {
        "primary": ["직접·간접비용", "의료자원 이용", "QALY", "ICER", "접근성·이용률"],
        "longterm": ["누적 의료비·자원 사용", "QALY", "비용효과(ICER)"],
        "horizon": "5년",
        "prom_role": "contextual", "reviewed": True,
    },
    "임플란트·기술": {
        "primary": ["implant survival", "revision", "방사선학적 이완·migration",
                    "정확도", "기기 관련 이상반응"],
        "longterm": ["implant survival", "누적 revision", "방사선학적 이완·migration"],
        "horizon": "5–10년",
        "prom_role": "secondary", "reviewed": True,
    },
    "스포츠 복귀": {
        "primary": ["복귀율", "복귀까지 시간", "이전 수준 복귀율", "활동 수준",
                    "복귀 유지", "재손상"],
        "longterm": ["복귀 유지율", "재손상률", "활동 수준 유지"],
        "horizon": "2–5년",
        "prom_role": "secondary", "reviewed": True,
    },
    "연골·생물학": {
        "primary": ["통증·기능 PROM", "치료 실패·재수술", "MRI 구조적 회복", "부작용"],
        "longterm": ["치료 실패·재수술", "MRI 구조적 회복 유지", "통증·기능 PROM의 장기 유지"],
        "horizon": "5년 이상",
        "prom_role": "primary", "reviewed": True,
    },
    "전방십자인대·반월상": {
        # PROM만으로 부족하다. 안정성·재수술·복귀가 함께 있어야 한다.
        "primary": ["IKDC/KOOS", "graft·repair failure", "재수술", "안정성",
                    "근력·기능검사", "스포츠 복귀"],
        "longterm": ["graft·repair failure", "누적 재수술", "관절염 진행", "복귀 유지"],
        "horizon": "5년 이상",
        "prom_role": "primary", "reviewed": True,
    },
    "회전근개·어깨": {
        # PROM과 구조적 치유는 서로 대체되지 않는다. 분리해 둔다.
        "primary": ["ASES/WORC/SSV 통증·기능", "재파열", "근력·ROM", "재수술·합병증"],
        "longterm": ["재파열", "누적 재수술", "통증·기능의 장기 유지"],
        "horizon": "2–5년",
        "prom_role": "primary", "reviewed": True,
    },
}

# 결과변수의 시간 속성. 문자열 blacklist 하나로는 안 된다 — 수술 당시의
# implant positioning accuracy는 장기 비호환이지만, 5년 재치환 위험모델의
# 5년 calibration은 장기 평가에 적합하다. 같은 단어라도 예측기간이 붙으면 달라진다.
#
#   index_event        수술 당시 한 번 재는 값 (재원기간, 당일 퇴원, 수술 시간)
#   short_term_only    정의상 단기 창에서만 재는 값 (30/90일 재입원)
#   cumulative         기간이 길수록 쌓이는 값 (누적 재수술, 누적 비용)
#   durability         유지되는지를 묻는 값 (implant survival, PROM 유지)
#   horizon_dependent  예측기간에 따라 달라지는 값 (calibration, discrimination)
TIME_ATTRIBUTE = {
    "index_event": ("재원기간", "당일 퇴원", "수술 시간", "수술 효율", "출혈",
                    "정확도", "outlier", "positioning", "기능회복 시간"),
    "short_term_only": ("30/90일", "90일", "30일", "응급실 방문"),
    "horizon_dependent": ("calibration", "discrimination", "AUC", "예측력", "예측기간"),
}
LONGTERM_OK = {"cumulative", "durability", "horizon_dependent"}


def time_attribute(outcome: str) -> str:
    """결과변수 하나의 시간 속성. 어디에도 안 걸리면 지속 가능한 값으로 본다."""
    lowered = outcome.lower()
    for kind, needles in TIME_ATTRIBUTE.items():
        if any(needle.lower() in lowered for needle in needles):
            return kind
    return "durability"


def longterm_outcomes(spec: dict) -> list[str]:
    """장기 질문에 쓸 결과변수. 명시가 없으면 primary에서 시간 속성으로 걸러 쓴다.

    horizon_dependent(calibration 등)는 통째로 막지 않는다. 예측기간이 길면
    장기 결과가 맞기 때문이다 — 그 판단은 horizon과 함께 문장에 드러낸다.
    """
    if spec.get("longterm"):
        return list(spec["longterm"])
    return [o for o in (spec.get("primary") or []) if time_attribute(o) in LONGTERM_OK]


# 장기 공백도 한 종류가 아니다. 같은 longterm_durability라도 무엇을 묻는지가 다르고,
# 그에 따라 설계가 달라진다.
LONGTERM_SUBTYPES = {
    "durability": "결과가 유지되는가 (생존·재파열)",
    "cumulative_risk": "기간이 길수록 쌓이는 위험 (누적 재수술·재입원·합병증)",
    "cumulative_cost_resource_use": "기간이 길수록 쌓이는 부담 (누적 의료비·자원 사용·QALY)",
    "sustained_recovery": "회복이 유지되는가 (PROM·기능 궤적)",
    "temporal_trend": "격차·비율이 시간에 따라 변하는가 (인구집단 추세)",
    "prediction_horizon": "예측기간 시점의 모델 성능",
}


def longterm_subtype(spec: dict, outcomes: list[str]) -> str:
    """장기 공백의 하위 유형. 설계를 정하는 근거라 카테고리만으로는 부족하다."""
    if spec.get("populationLevel"):
        return "temporal_trend"
    blob = " ".join(outcomes)
    if any(time_attribute(o) == "horizon_dependent" for o in outcomes):
        return "prediction_horizon"
    # 비용은 위험이 아니라 부담이다. 통계 설계가 달라진다(생존분석 vs 누적 비용 모형).
    if any(word in blob for word in ("의료비", "비용", "자원 사용", "QALY", "ICER")):
        return "cumulative_cost_resource_use"
    if "누적" in blob:
        return "cumulative_risk"
    if any(word in blob for word in ("PROM", "회복", "기능", "만족도", "활동 수준")):
        return "sustained_recovery"
    return "durability"


def horizon(spec: dict) -> str:
    """이 분야에서 '장기'가 몇 년인지. 5년으로 고정하면 분야마다 틀린다."""
    return spec.get("horizon") or "5년"


def variant_sets(spec: dict, documents: list[tuple[str, str]]) -> dict[str, set[str]]:
    """변이별로 걸리는 **고유 논문 PMID 집합**.

    용어 출현 횟수로 세면 안 된다. 한 논문이 access 용어를 다섯 번 쓰면 다섯 편처럼
    보인다. 실제로 형평성 59편에서 출현 횟수는 55:29였지만 논문 수는 36:17이었고,
    그중 13편은 양쪽에 다 걸렸다. 우세 판정과 최소 표본 기준은 둘 다 논문 수로 재야 한다.
    """
    out: dict[str, set[str]] = {}
    for name, variant in (spec.get("variants") or {}).items():
        terms = [t.lower() for t in variant["terms"]]
        out[name] = {pmid for pmid, text in documents if any(t in text.lower() for t in terms)}
    return out


def variant_breakdown(sets: dict[str, set[str]], total: set[str]) -> dict:
    """겹침을 그대로 기록한다. 어느 쪽에도 안 걸린 논문 수까지 남긴다.

    양쪽에 다 걸린 논문은 잘못 센 것이 아니라 두 질문에 모두 해당하는 다중 라벨
    논문이다. 형평성이라면 "접근성 차이가 실제 회복 격차로 이어지는가"를 다룬
    연결 문헌일 수 있어, 오히려 가장 값진 신호다. 버리지 않고 겹친다는 사실만 남긴다.
    """
    names = list(sets)
    covered = set().union(*sets.values()) if sets else set()
    breakdown = {"total": len(total), "neither": len(total - covered)}
    for name in names:
        others = set().union(*(sets[n] for n in names if n != name)) if len(names) > 1 else set()
        breakdown[f"{name}_matched"] = len(sets[name])
        breakdown[f"{name}_only"] = len(sets[name] - others)
    if len(names) > 1:
        from . import config
        overlap = set.intersection(*sets.values())
        breakdown["overlapCount"] = len(overlap)
        # 분모를 둘 다 남긴다. 어느 쪽에도 안 걸린 논문이 많으면 두 값이 크게 벌어진다.
        #   overlapAmongMatched  두 하위집단 합집합 대비 (Jaccard)
        #   overlapAmongCluster  클러스터 전체 논문 대비
        breakdown["overlapAmongMatched"] = round(len(overlap) / max(len(covered), 1), 3)
        breakdown["overlapAmongCluster"] = round(len(overlap) / max(len(total), 1), 3)
        # 1편이라도 겹치면 무조건 종속으로 보는 것은 과하다. 임계값으로 판단한다.
        breakdown["independentSubgroups"] = (
            breakdown["overlapAmongMatched"] <= config.SUBGROUP_OVERLAP_MAX)
    return breakdown


def resolve(topic: str, documents: list[tuple[str, str]]) -> dict:
    """클러스터의 하위 유형을 문헌에서 정한다.

    형평성처럼 한 이름 아래 성격이 다른 연구가 섞이는 클러스터가 있다. 접근성
    연구와 수술 후 결과 격차 연구는 1차 결과도 PROM의 위치도 다르므로, 그 묶음에
    실제로 무엇이 많은지 **고유 논문 수로** 세어 정한다.

    1위가 뚜렷하지 않으면 임의로 한쪽에 보내지 않고 mixed로 둔다. 이때 PROM 역할은
    보수적인 기본값(primary가 아님)을 유지하고, 판정 프롬프트에 섞여 있다는 사실을
    알려 uncertain 쪽으로 기울게 한다.
    """
    from . import config
    spec = canonical(topic)
    if not spec.get("variants") or not documents:
        return spec
    sets = variant_sets(spec, documents)
    counts = {name: len(pmids) for name, pmids in sets.items()}
    breakdown = variant_breakdown(sets, {pmid for pmid, _ in documents})
    if not counts or not max(counts.values()):
        return spec
    best = max(counts, key=lambda name: counts[name])
    others = sorted((v for k, v in counts.items() if k != best), reverse=True)
    if others and counts[best] < others[0] * config.SUBTYPE_DOMINANCE_RATIO:
        return {**spec, "variant": "mixed", "variantCounts": counts, "variantBreakdown": breakdown,
                "independentSubgroups": breakdown.get("independentSubgroups", True),
                "subtypeNote": "이 묶음에는 " + " / ".join(
                    f"{v['label']}({counts[k]}편)" for k, v in spec["variants"].items())
                + f" 연구가 비슷한 비중으로 섞여 있습니다(양쪽 모두 해당 {breakdown.get('both', 0)}편). "
                  "1차 결과가 서로 다르므로 하나의 정의로 판정할 수 없습니다."}
    return {**spec, **spec["variants"][best], "variant": best,
            "variantCounts": counts, "variantBreakdown": breakdown,
            "independentSubgroups": breakdown.get("independentSubgroups", True)}


PROM_GAP_HARD_BLOCK = {"not_applicable"}
# 이 역할에서는 근거가 강할 때만 후보로 올린다(표본 충분 + 효과크기 중간 이상).
PROM_GAP_NEEDS_STRONG = {"contextual"}
# 이 역할에서 나온 PROM 공백은 최종 점수에서 감점한다.
PROM_GAP_PENALTY = {"secondary": 2, "contextual": 1}


def canonical(topic: str) -> dict:
    """분야 정의. 없으면 판정을 보류시키는 빈 정의를 준다(임의 추정하지 않는다)."""
    return CANONICAL_OUTCOMES.get(topic) or {"primary": [], "prom_role": "contextual", "reviewed": False}


# ---------------------------------------------------------------------------
# 카테고리별 구조적 사유
#
# 구조적 공백은 PROM에만 생기지 않는다. 판정자에게 카테고리마다 "이 종류에서
# 흔히 구조적인 경우"를 알려 줘야, 9개 탐지기 전부에 같은 게이트가 걸린다.
# ---------------------------------------------------------------------------

STRUCTURAL_PRIORS = {
    "outcome_measurement":
        "그 분야의 1차 결과가 애초에 다른 지표일 때. 예) 감염 연구의 1차 결과는 균 박멸이지 환자보고 점수가 아니다.",
    "methodology":
        "무작위배정이 윤리적·현실적으로 불가능할 때. 예) 발생률이 1~2%인 희귀 합병증, 응급 수술, "
        "환자가 배정을 거부하는 술기 선택.",
    "comparator":
        "연구 단계상 비교군이 아직 없을 때. 예) 단일군 feasibility·초기 안전성 연구, 표준 대안이 없는 술기.",
    "population_external_validity":
        "그 환자군이 해당 술기의 대상이 아닐 때, 또는 단일 기관 자료만 존재하는 것이 자연스러운 신기술 초기 단계.",
    "clinical_utility_implementation":
        "기술이 아직 타당성 검증 단계라 환자 결과까지 볼 시점이 아닐 때.",
    "longterm_durability":
        "그 술기·기술이 도입된 지 얼마 안 돼 장기 자료가 물리적으로 존재할 수 없을 때. "
        "예) 최근 5년 내 상용화된 로봇 플랫폼의 10년 생존율.",
}



# ---------------------------------------------------------------------------
# 카테고리별 탐지 어휘
#
# 6개 공백 카테고리마다 "이 논문이 그것을 다뤘는가"를 판정할 표지가 필요하다.
# 없으면 카테고리는 이름표일 뿐 탐지되지 않는다.
# ---------------------------------------------------------------------------

# longterm_durability — 장기 추적을 실제로 보고했는가
LONGTERM_TERMS = [
    "long-term", "long term", "survivorship", "survival rate", "implant survival",
    "5-year", "five-year", "10-year", "ten-year", "15-year", "20-year",
    "minimum 5 year", "minimum five year", "minimum 10 year", "minimum ten year",
    "kaplan-meier", "cumulative incidence", "late failure", "late revision",
    "durability", "registry study",
]

# clinical_utility_implementation — 정확도 지표만 있는가, 환자 결과까지 갔는가
ACCURACY_TERMS = [
    "accuracy", "auc", "area under the curve", "c-statistic", "discrimination",
    "calibration", "sensitivity", "specificity", "precision", "f1 score",
    "root mean square", "mean absolute error", "outlier", "malalignment",
    "radiographic accuracy", "component position", "target zone",
]
PATIENT_OUTCOME_TERMS = [
    "complication", "revision", "reoperation", "readmission", "mortality",
    "length of stay", "return to work", "return to sport", "pain score",
    "functional outcome", "clinical outcome", "survivorship",
    "change in management", "decision impact", "clinical utility", "net benefit",
    "decision curve",
]

# population_external_validity — 외부 코호트에서 다시 확인했는가
EXTERNAL_VALIDATION_TERMS = [
    "external validation", "externally validated", "independent cohort",
    "multicenter", "multicentre", "multi-institutional", "registry",
    "temporal validation", "geographic validation", "generalizability",
    "validation cohort", "held-out",
]

# comparator — 선택지끼리 직접 비교했는가
DIRECT_COMPARISON_TERMS = [
    "versus", " vs ", " vs.", "compared with", "compared to", "comparative",
    "head-to-head", "randomized controlled", "randomised controlled",
    "propensity-matched", "propensity score matched", "matched cohort",
    "non-inferiority", "superiority trial", "two groups", "three groups",
]

# 기술 클러스터. 이 주제들은 "정확도는 좋아졌는데 환자에게 도움이 되는가"가
# 고유한 공백이라 clinical_utility_implementation 탐지를 따로 돌린다.
TECHNOLOGY_CLUSTERS = {"AI·예측", "로봇·내비게이션"}
