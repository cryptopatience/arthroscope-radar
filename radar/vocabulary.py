"""탐지에 쓰는 어휘와 분야별 기준 결과변수.

analysis.py에서 분리한 이유는 두 가지다.

1. 이 사전을 고치면 과거 판정이 무효가 된다. 버전을 달아 캐시 키에 넣어야 한다.
2. 여기 있는 값은 통계가 아니라 임상 지식이다. 코드와 섞여 있으면 전문가가 고치기 어렵다.

사전을 고칠 때는 반드시 아래 버전을 올린다. 올리지 않으면 옛 판정이 그대로 재사용된다.
"""
from __future__ import annotations

# 용어 사전을 고치면 올린다.
KEYWORD_DICT_VERSION = "1"
# 분야별 기준 결과변수(CANONICAL_OUTCOMES)를 고치면 올린다.
CANONICAL_OUTCOME_VERSION = "4"


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
        "prom_role": "secondary", "reviewed": True,
    },
    "재치환·전환 수술": {
        "primary": ["재치환 생존", "합병증", "기능 회복", "PROM"],
        "prom_role": "primary", "reviewed": True,
    },
    "로봇·내비게이션": {
        "primary": ["정렬 정확도", "outlier 비율", "합병증", "수술 효율"],
        # 정확도·수술 효율은 한 시점 지표라 장기 질문의 결과변수가 될 수 없다.
        "longterm": ["임플란트 생존", "누적 재수술", "후기 이완·migration"],
        "prom_role": "contextual", "reviewed": True,
    },
    "정렬·생체역학": {
        "primary": ["정렬 정확도", "outlier 비율", "합병증", "수술 효율"],
        "longterm": ["임플란트 생존", "누적 재수술", "후기 이완·migration"],
        "prom_role": "contextual", "reviewed": True,
    },
    "AI·예측": {
        "primary": ["discrimination (AUC)", "calibration", "external validation", "clinical utility"],
        # 모델 성능 지표는 시점 지표다. 장기 질문은 성능이 시간에 따라 유지되는지를 묻는다.
        "longterm": ["성능 유지(temporal validation)", "임상 결과 예측력"],
        "prom_role": "contextual", "reviewed": True,
    },
    "형평성": {
        "primary": ["치료 접근성", "치료 격차", "회복 격차"],
        # 접근성 격차의 5년 변화(반복 단면)와 수술 후 장기 회복 격차(종단 코호트)는
        # 설계가 다른 별개 질문이다. 한 카드에서 섞지 않는다. 이 코퍼스는 수술 문헌이므로
        # 종단 코호트 쪽을 잡는다.
        "longterm": ["집단 간 회복 격차 (PROM·기능 궤적 차이)"],
        "longtermDesign": "수술받은 환자의 종단 코호트 (집단 간 회복 궤적 비교)",
        "prom_role": "primary", "reviewed": True,
    },
    "환자요인": {
        "primary": ["위험도", "회복", "합병증", "PROM"],
        "prom_role": "contextual", "reviewed": True,
    },
    "재수술·합병증": {
        # 클러스터가 이차적이라는 뜻이 아니라, 이 분야에서 PROM이 이차적이라는 뜻이다.
        "primary": ["재수술·재치환 발생률", "revision-free survival", "합병증률",
                    "원인별 실패", "implant survival"],
        "prom_role": "secondary", "reviewed": True,
    },
    "PROM·기대치": {
        "primary": ["PROM 변화", "기대-결과 불일치", "기대 충족률", "만족도",
                    "MCID/SCB/PASS 달성률"],
        "longterm": ["PROM 유지", "만족도 유지"],
        "prom_role": "primary", "reviewed": True,
    },
    "외래·회복": {
        "primary": ["당일 퇴원 성공·실패", "재원기간", "응급실 방문",
                    "30/90일 재입원·합병증", "기능회복 시간"],
        # 재원기간·당일 퇴원은 수술 당시 한 번 재는 지표다. 5년 질문에는 누적 지표를 쓴다.
        "longterm": ["누적 재입원·재수술·합병증", "PROM 유지"],
        "prom_role": "secondary", "reviewed": True,
    },
    "비용·보건정책": {
        "primary": ["직접·간접비용", "의료자원 이용", "QALY", "ICER", "접근성·이용률"],
        "longterm": ["누적 의료비·자원 사용", "QALY", "비용효과(ICER)"],
        "prom_role": "contextual", "reviewed": True,
    },
    "임플란트·기술": {
        "primary": ["implant survival", "revision", "방사선학적 이완·migration",
                    "정확도", "기기 관련 이상반응"],
        "longterm": ["implant survival", "누적 revision", "방사선학적 이완·migration"],
        "prom_role": "secondary", "reviewed": True,
    },
    "스포츠 복귀": {
        "primary": ["복귀율", "복귀까지 시간", "이전 수준 복귀율", "활동 수준",
                    "복귀 유지", "재손상"],
        "longterm": ["복귀 유지율", "재손상률", "활동 수준 유지"],
        "prom_role": "secondary", "reviewed": True,
    },
    "연골·생물학": {
        "primary": ["통증·기능 PROM", "치료 실패·재수술", "MRI 구조적 회복", "부작용"],
        "longterm": ["치료 실패·재수술", "MRI 구조적 회복 유지", "통증·기능 PROM 유지"],
        "prom_role": "primary", "reviewed": True,
    },
    "전방십자인대·반월상": {
        # PROM만으로 부족하다. 안정성·재수술·복귀가 함께 있어야 한다.
        "primary": ["IKDC/KOOS", "graft·repair failure", "재수술", "안정성",
                    "근력·기능검사", "스포츠 복귀"],
        "longterm": ["graft·repair failure", "누적 재수술", "관절염 진행", "복귀 유지"],
        "prom_role": "primary", "reviewed": True,
    },
    "회전근개·어깨": {
        # PROM과 구조적 치유는 서로 대체되지 않는다. 분리해 둔다.
        "primary": ["ASES/WORC/SSV 통증·기능", "재파열", "근력·ROM", "재수술·합병증"],
        "longterm": ["재파열", "누적 재수술", "통증·기능 유지"],
        "prom_role": "primary", "reviewed": True,
    },
}

# 장기 질문에 쓸 수 없는 결과변수. 한 시점에서만 재는 지표라 "5년 시점의 재원기간"
# 같은 말이 안 되는 문장이 나온다. longterm 값에는 기간을 넣지 않는다 —
# 기간은 템플릿이 붙이므로 "5년 시점의 5년 누적…"처럼 겹친다. longterm 키가 없는 클러스터는 primary에서
# 이것들을 걸러 쓰고, 걸러서 아무것도 안 남으면 장기 공백을 만들지 않는다.
LONGTERM_INCOMPATIBLE = (
    "재원기간", "당일 퇴원", "응급실 방문", "수술 효율", "수술 시간", "출혈",
    "정확도", "outlier", "calibration", "discrimination", "external validation",
    "기능회복 시간", "접근성·이용률",
)


def longterm_outcomes(topic: str) -> list[str]:
    """장기 질문에 쓸 결과변수. 명시가 없으면 primary에서 시점 지표를 걸러 쓴다."""
    spec = canonical(topic)
    if spec.get("longterm"):
        return list(spec["longterm"])
    return [o for o in (spec.get("primary") or [])
            if not any(bad in o for bad in LONGTERM_INCOMPATIBLE)]


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
