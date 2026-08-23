"""PubMed 수집 → 초록 구조화 → 주제 분류 → 트렌드 → 연구 아이디어 (lib/analysis.ts 포팅).

Streamlit 앱과 일일 스냅샷 스크립트가 같은 run_analysis를 호출하므로
저장된 결과와 실시간 결과가 어긋나지 않는다.
"""
from __future__ import annotations

import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from .ncbi import EUTILS, NcbiCredentials, decode_xml, fetch_ncbi_text, ncbi_params

# ---------------------------------------------------------------------------
# 저널·계열·주제 정의
# ---------------------------------------------------------------------------

# 두 계열은 서로 다른 질문에 답한다 — 한쪽은 임플란트 생존·재수술, 다른 쪽은
# 봉합·스포츠 복귀 — 그래서 합쳐서 계산한 신호는 보통 두 문헌의 평균일 뿐이다.
FAMILIES = {
    "arthroplasty": {"label": "관절성형 계열", "short": "관절성형"},
    "arthroscopy": {"label": "관절경·스포츠의학 계열", "short": "관절경"},
}
FAMILY_ORDER = ["arthroplasty", "arthroscopy"]

JOURNALS = {
    "joa": {"label": "The Journal of Arthroplasty", "short": "JOA", "query": '"J Arthroplasty"[jour]',
            "aliases": ["j arthroplasty", "the journal of arthroplasty"], "family": "arthroplasty"},
    "at": {"label": "Arthroplasty Today", "short": "AT", "query": '"Arthroplast Today"[jour]',
           "aliases": ["arthroplast today", "arthroplasty today"], "family": "arthroplasty"},
    "bjj": {"label": "The Bone & Joint Journal", "short": "BJJ", "query": '"Bone Joint J"[jour]',
            "aliases": ["bone joint j", "the bone & joint journal", "the bone and joint journal"], "family": "arthroplasty"},
    "jbjsam": {"label": "Journal of Bone and Joint Surgery (American)", "short": "JBJS Am", "query": '"J Bone Joint Surg Am"[jour]',
               "aliases": ["j bone joint surg am", "the journal of bone and joint surgery. american volume"], "family": "arthroplasty"},
    "corr": {"label": "Clinical Orthopaedics and Related Research", "short": "CORR", "query": '"Clin Orthop Relat Res"[jour]',
             "aliases": ["clin orthop relat res", "clinical orthopaedics and related research"], "family": "arthroplasty"},
    "acta": {"label": "Acta Orthopaedica", "short": "Acta Orthop", "query": '"Acta Orthop"[jour]',
             "aliases": ["acta orthop", "acta orthopaedica"], "family": "arthroplasty"},
    "kssta": {"label": "Knee Surgery, Sports Traumatology, Arthroscopy", "short": "KSSTA",
              "query": '"Knee Surg Sports Traumatol Arthrosc"[jour]',
              "aliases": ["knee surg sports traumatol arthrosc", "knee surgery, sports traumatology, arthroscopy"], "family": "arthroscopy"},
    "arthroscopy": {"label": "Arthroscopy", "short": "ARTH", "query": '"Arthroscopy"[jour]',
                    "aliases": ["arthroscopy",
                                "arthroscopy : the journal of arthroscopic & related surgery : official publication of the arthroscopy association of north america and the international arthroscopy association"],
                    "family": "arthroscopy"},
    "ajsm": {"label": "The American Journal of Sports Medicine", "short": "AJSM", "query": '"Am J Sports Med"[jour]',
             "aliases": ["am j sports med", "the american journal of sports medicine"], "family": "arthroscopy"},
    "ojsm": {"label": "Orthopaedic Journal of Sports Medicine", "short": "OJSM", "query": '"Orthop J Sports Med"[jour]',
             "aliases": ["orthop j sports med", "orthopaedic journal of sports medicine"], "family": "arthroscopy"},
}
JOURNAL_ORDER = list(JOURNALS.keys())

TOPICS = [
    {"label": "AI·예측", "terms": ["artificial intelligence", "machine learning", "deep learning", "algorithm", "prediction model", "large language model", "computer vision"]},
    {"label": "로봇·내비게이션", "terms": ["robotic", "robot-assisted", "navigation", "computer-assisted", "augmented reality", "patient-specific instrumentation"]},
    {"label": "감염", "terms": ["infection", "periprosthetic joint infection", "pji", "antibiotic", "septic", "microbiology"]},
    {"label": "재수술·합병증", "terms": ["revision", "reoperation", "complication", "failure", "fracture", "readmission", "conversion", "instability"]},
    # 초록은 "PROM"이라 쓰지 않고 도구명만 적는 경우가 많다. 무릎에서 실제로 쓰이는
    # 도구를 직접 넣어야 한다. 전문 400편 실측에서 도구명만 있는 초록의 29%를 놓치고 있었다.
    {"label": "PROM·기대치", "terms": [
        "patient-reported", "patient reported", "prom", "promis", "expectation", "satisfaction",
        "minimal clinically important", "mcid", "quality of life", "patient acceptable symptom",
        "koos", "womac", "oxford knee", "forgotten joint", "ikdc", "lysholm", "tegner", "kujala",
        "hoos", "marx activity", "knee society score", "eq-5d", "euroqol", "sf-36", "sf-12", "vr-12",
        "visual analog*", "visual analogue*", "numeric rating scale"]},
    {"label": "정렬·생체역학", "terms": ["alignment", "kinematic*", "biomechanic*", "balance", "gait", "range of motion", "component position"]},
    {"label": "외래·회복", "terms": ["outpatient", "same-day", "enhanced recovery", "length of stay", "discharge", "opioid", "rehabilitation"]},
    {"label": "비용·보건정책", "terms": ["cost", "economic", "value", "bundled payment", "health care utilization", "resource utilization"]},
    {"label": "형평성·환자요인", "terms": ["disparit*", "race", "ethnic*", "sex difference", "social determinant", "frailty", "obesity", "diabet*"]},
    {"label": "임플란트·기술", "terms": ["implant", "bearing", "cementless", "fixation", "polyethylene", "sensor", "wearable", "smartphone"]},
    {"label": "스포츠 복귀", "terms": ["return to sport", "return to play", "athlete", "sports participation", "performance"]},
    {"label": "연골·생물학", "terms": ["cartilage", "biologic*", "platelet-rich plasma", "stem cell", "stromal vascular", "bone marrow", "scaffold"]},
    {"label": "전방십자인대·반월상", "terms": ["anterior cruciate", "acl", "aclr", "menisc*", "root repair"]},
    {"label": "회전근개·어깨", "terms": ["rotator cuff", "shoulder", "labral", "biceps tendon"]},
]

# 해부학적 구조를 이름으로 갖는 주제. 회전근개 연구를 "무릎으로 확장"하자는 것은
# 연구 공백이 아니라 범주 오류이므로, 관절이 충돌하면 탐지기에서 제외한다.
TOPIC_JOINT = {"회전근개·어깨": "어깨", "전방십자인대·반월상": "무릎"}

# 이 레이더는 무릎에 대한 것이다. 앱 전체의 대상을 바꾸려면 이 한 줄만 바꾸면 된다.
TARGET_JOINT = "무릎"

# 하위집단 축. 무릎 하나만 분석하므로 "다른 관절로 확장" 공백은 성립하지 않는다.
# 대신 무릎 안에서 실제로 결과가 갈리는 환자군을 축으로 삼는다.
# TOPICS와 겹치는 축(성별·비만 등 형평성·환자요인)은 교차 공백 규칙과 중복되므로 뺐다.
SUBGROUPS = [
    {"label": "고령 환자", "terms": ["elderly", "octogenarian", "nonagenarian", "geriatric",
                                 "older adult*", "advanced age", "age 75", "age 80", "aged 80"]},
    {"label": "젊은 환자", "terms": ["young patient*", "younger patient*", "adolescent", "juvenile",
                                 "skeletally immature", "under 50 years", "under 55 years", "age 50 or younger"]},
    # family가 있으면 그 계열 문헌이 주류인 주제에만 적용한다. 재치환은 인공관절 개념이라
    # "ACL 재건 연구에서 재치환 환자군" 같은 범주 오류를 막아야 한다.
    {"label": "재치환·전환 수술", "family": "arthroplasty",
     "terms": ["revision arthroplasty", "revision total knee", "revision tka",
               "re-revision", "conversion to arthroplasty", "revision surgery"]},
    {"label": "양측 동시 수술", "family": "arthroplasty",
     "terms": ["bilateral", "simultaneous bilateral", "staged bilateral"]},
]

MONTHS = {"jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
          "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12"}

# ---------------------------------------------------------------------------
# 수집 상수
# ---------------------------------------------------------------------------
ESEARCH_PAGE_SIZE = 1000
MAX_TOTAL_ARTICLES = 6000        # 안전 상한. 넘으면 균등 표본.
ABSTRACT_PREVIEW_CHARS = 420
EFETCH_BATCH_SIZE = 400
EFETCH_CONCURRENCY = 3           # API 키가 있을 때만. 5는 NCBI가 400으로 밀어냈다.
EFETCH_MIN_YIELD = 0.9
EFETCH_PARSE_ATTEMPTS = 3
ESEARCH_ATTEMPTS = 3

# 트렌드·아이디어 상수
MIN_TREND_COUNT = 20
MIN_TREND_SHARE = 3
# 반기별 최소 편수. 전반기 3편 → 후반기 5편은 +60%지만 잡음이다. 교차 공백 규칙이
# rising을 전제로 하므로 이 잡음이 아이디어까지 그대로 전파된다.
MIN_TREND_HALF = 8
MIN_IDEAS_FOR_SCOPE = 2
PROSPECTIVE_DESIGNS = ("전향적 연구", "무작위시험")
# 사설·정오표·종설·술기 보고·기초연구는 "전향 연구가 적다"의 분모가 될 수 없다.
# 트렌드 표와 초록 목록에는 그대로 남기고, 아이디어 생성에서만 제외한다.
NON_CLINICAL_DESIGNS = ("논평·기타", "종설", "체계적 문헌고찰", "기초·생체역학", "술기 보고", "증례 보고")
IDEA_MIN_POOL = 15
# 공백 판정은 절대 임계값 대신 "코퍼스의 나머지 대비 유의하게 낮은가"로 본다.
# 절대값(예: PROM 30%)을 쓰면 사전 커버리지가 좋아질 때 규칙이 조용히 망가진다.
GAP_Z = 1.96            # 단측이 아니라 보수적으로 1.96(약 p<0.025)을 넘겨야 공백으로 인정
# 통계적 유의성만 보면 큰 주제가 작은 격차로도 통과한다(1,000편에서 7%p 차이도 z>5).
# 그래서 "기준선의 이 비율 이하"라는 실질 격차 하한을 함께 건다.
GAP_MIN_RATIO = 0.75
IDEA_MAX = 8
IDEA_PER_KIND = 4       # 같은 종류(outcome·design·joint·intersection)를 몇 개까지 뽑을지
IDEA_PER_LEAD = 2       # 같은 주제를 몇 개까지 뽑을지
GAP_RATIO = 0.35
# 공백 하나마다 "이 판정을 얼마나 믿을 수 있는가"의 재료를 함께 계산한다.
# 표본 충분성은 편수와 효과크기(Cohen's h)를 같이 본다. 편수만 보면 큰 주제의
# 미세한 격차가 "충분"으로 올라오고, 효과크기만 보면 20편짜리 주제의 우연이 올라온다.
GAP_N_SOLID = 50
GAP_N_LIMITED = 25
GAP_H_SOLID = 0.5       # Cohen's h 관례: 0.2 작음 · 0.5 중간 · 0.8 큼
GAP_H_LIMITED = 0.2
# 시간적 근거: 같은 공백을 전반기·후반기로 나눠 다시 재고 격차가 움직였는지 본다.
GAP_TEMPORAL_MIN_HALF = 8   # 한쪽 반기 표본이 이보다 적으면 방향을 말하지 않는다
GAP_TEMPORAL_SHIFT = 0.05   # 격차가 5%p 이상 움직여야 좁혀졌다·벌어졌다고 부른다


class AnalysisError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass
class Article:
    pmid: str
    title: str
    abstract: str
    journal: str
    journalKey: str
    date: str
    authors: str
    doi: str | None
    topics: list[str]
    joint: str
    design: str


@dataclass
class Trend:
    label: str
    count: int
    previous: int
    recent: int
    delta: int
    share: int
    signal: str  # rising | steady | cooling | sparse


@dataclass
class Idea:
    id: str
    title: str
    rationale: str
    pico: str
    design: str
    primaryEndpoint: str
    novelty: int
    feasibility: int
    evidence: list[dict] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    # 이 공백을 얼마나 단단히 측정했는지. 화면의 "표본 충분성·시간적 근거"가 여기서 나온다.
    metrics: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# XML 파싱
# ---------------------------------------------------------------------------

def _first(block: str, tag: str) -> str:
    m = re.search(rf"<{tag}(?:\s[^>]*)?>([\s\S]*?)</{tag}>", block, re.I)
    return decode_xml(m.group(1)) if m else ""


def _all(block: str, tag: str) -> list[str]:
    return [decode_xml(m.group(1)) for m in re.finditer(rf"<{tag}(?:\s[^>]*)?>([\s\S]*?)</{tag}>", block, re.I)]


def _parse_date(block: str) -> str:
    article_date = re.search(r"<ArticleDate[^>]*>([\s\S]*?)</ArticleDate>", block, re.I)
    pub_date = re.search(r"<PubDate[^>]*>([\s\S]*?)</PubDate>", block, re.I)
    source = (article_date.group(1) if article_date else "") or (pub_date.group(1) if pub_date else "")
    year = _first(source, "Year")
    if not year:
        m = re.search(r"\d{4}", _first(source, "MedlineDate"))
        year = m.group(0) if m else ""
    raw_month = (_first(source, "Month") or "01").lower()
    month = raw_month.zfill(2) if raw_month.isdigit() else MONTHS.get(raw_month[:3], "01")
    day = (_first(source, "Day") or "01").zfill(2)
    return f"{year}-{month}-{day}" if year else ""


def journal_key_for(name: str) -> str | None:
    """별칭은 정확히 일치해야 한다. 부분 일치는 'Arthroscopy Techniques'를 Arthroscopy로 분류한다."""
    normalized = name.strip().lower()
    for key, journal in JOURNALS.items():
        if normalized in journal["aliases"]:
            return key
    return None


# 용어는 단어 단위로 일치하며 복수형을 허용한다. 접두어 매칭은 "tha"가 "that"에
# 걸리게 했다. 진짜 어간인 용어("menisc")는 끝에 *를 붙인다.
_term_patterns: dict[str, re.Pattern] = {}
_count_patterns: dict[str, re.Pattern] = {}


def _pattern(term: str, cache: dict) -> re.Pattern:
    pattern = cache.get(term)
    if pattern is None:
        is_stem = term.endswith("*")
        body = re.escape(term[:-1] if is_stem else term)
        pattern = re.compile(r"\b" + body + ("" if is_stem else r"(?:s|es)?\b"))
        cache[term] = pattern
    return pattern


def _matches_term(text: str, term: str) -> bool:
    return bool(_pattern(term, _term_patterns).search(text))


def _count_term(text: str, term: str) -> int:
    return len(_pattern(term, _count_patterns).findall(text))


def classify_topics(text: str) -> list[str]:
    normalized = text.lower()
    topics = [t["label"] for t in TOPICS if any(_matches_term(normalized, term) for term in t["terms"])]
    return topics or ["기타 임상연구"]


_JOINT_TERMS = [
    ("무릎", ["knee", "tka", "uka", "acl", "aclr", "menisc*", "patell*"]),
    ("고관절", ["hip", "tha", "acetabul*", "femoroacetabular"]),
    ("어깨", ["shoulder", "rotator cuff", "glenoid", "labral"]),
    ("발목·족부", ["ankle", "foot", "achilles"]),
]


def classify_joint(text: str) -> str:
    """관절이 언급된 횟수로 점수화. 2위보다 1.5배 이상 명확해야 그 관절로 본다."""
    normalized = text.lower()
    scores = sorted(
        ((label, sum(_count_term(normalized, t) for t in terms)) for label, terms in _JOINT_TERMS),
        key=lambda x: -x[1],
    )
    if not scores[0][1]:
        return "기타·다관절"
    if len(scores) > 1 and scores[0][1] < scores[1][1] * 1.5:
        return "기타·다관절"
    return scores[0][0]


def _family_share(items: list[Article], family: str) -> float:
    if not items:
        return 0.0
    return sum(1 for a in items if JOURNALS[a.journalKey]["family"] == family) / len(items)


def classify_subgroups(text: str) -> list[str]:
    normalized = text.lower()
    return [g["label"] for g in SUBGROUPS if any(_matches_term(normalized, t) for t in g["terms"])]


def classify_design(text: str, publication_types: list[str]) -> str:
    """PublicationType은 NLM이 색인 단계에서 붙인 값이라 본문 표현보다 신뢰도가 높다.
    둘을 한 문자열로 합쳐 매칭하면 본문의 "review of the literature" 같은 표현이
    종설로 오인되므로 분리해서 본다."""
    types = {t.strip().lower() for t in publication_types}
    n = text.lower()

    if types & {"editorial", "comment", "letter", "published erratum", "news", "retraction of publication"}:
        return "논평·기타"
    if "case reports" in types:
        return "증례 보고"
    if types & {"meta-analysis", "systematic review"} or "meta-analysis" in n or "systematic review" in n:
        return "체계적 문헌고찰"
    if "randomized controlled trial" in types or re.search(r"randomi[sz]ed controlled trial", n):
        return "무작위시험"
    if "cadaver" in n or "biomechanic" in n or "in vitro" in n or "finite element" in n:
        return "기초·생체역학"
    if "prospective" in n:
        return "전향적 연구"
    if "controlled clinical trial" in types or re.search(r"randomi[sz]ed", n):
        return "무작위시험"
    if "cross-sectional" in n or "survey" in n or "questionnaire" in n or "delphi" in n or "consensus statement" in n:
        return "단면·설문"
    if "registry" in n or "database" in n or "national inpatient" in n:
        return "등록·데이터베이스"
    if "retrospective" in n or "case series" in n or "case-control" in n:
        return "후향적 연구"
    # "cohort"만 있고 시점 표현이 없으면 후향으로 단정하지 않는다. 실제로 142편이 여기 해당했다.
    if "cohort" in n or "observational study" in types:
        return "관찰 코호트"
    if "technical note" in n or "surgical technique" in n:
        return "술기 보고"
    if "review" in types:
        return "종설"
    return "기타 임상연구"


def parse_articles(xml: str, unknown_journals: set[str] | None = None) -> list[Article]:
    out: list[Article] = []
    for m in re.finditer(r"<PubmedArticle>([\s\S]*?)</PubmedArticle>", xml, re.I):
        block = m.group(1)
        title = _first(block, "ArticleTitle") or "제목 없음"
        parts = []
        for attrs, body in re.findall(r"<AbstractText([^>]*)>([\s\S]*?)</AbstractText>", block, re.I):
            label = re.search(r'Label="([^"]+)"', attrs, re.I)
            value = decode_xml(body)
            parts.append(f"{label.group(1)}: {value}" if label else value)
        abstract = " ".join(parts)
        journal = _first(block, "ISOAbbreviation") or _first(block, "Title") or "Unknown journal"
        author_blocks = re.findall(r"<Author(?:\s[^>]*)?>([\s\S]*?)</Author>", block, re.I)[:3]
        names = [f"{_first(a, 'LastName')} {_first(a, 'Initials')}".strip() for a in author_blocks]
        names = [n for n in names if n]
        authors = ", ".join(names)
        doi_m = re.search(r'<ArticleId[^>]*IdType="doi"[^>]*>([\s\S]*?)</ArticleId>', block, re.I)
        pmid = _first(block, "PMID")
        if not pmid:
            continue
        key = journal_key_for(journal)
        if not key:
            if unknown_journals is not None:
                unknown_journals.add(journal)
            continue
        text = f"{title} {abstract}"
        out.append(Article(
            pmid=pmid, title=title, abstract=abstract, journal=journal, journalKey=key,
            date=_parse_date(block),
            authors=(f"{authors}{' 외' if len(author_blocks) >= 3 else ''}" if authors else "저자 정보 없음"),
            doi=decode_xml(doi_m.group(1)) if doi_m else None,
            topics=classify_topics(text), joint=classify_joint(text),
            design=classify_design(text, _all(block, "PublicationType")),
        ))
    return out


# ---------------------------------------------------------------------------
# 트렌드
# ---------------------------------------------------------------------------

def _ts(date: str) -> float:
    try:
        return datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return float("nan")


def build_trends(articles: list[Article], date_from: str, date_to: str) -> list[Trend]:
    start = _ts(date_from)
    end = _ts(date_to) + 86399
    midpoint = start + (end - start) / 2
    stamps = {a.pmid: _ts(a.date) for a in articles}
    recent_total = max(sum(1 for a in articles if stamps[a.pmid] >= midpoint), 1)
    previous_total = max(sum(1 for a in articles if stamps[a.pmid] < midpoint), 1)
    rows: list[Trend] = []
    for topic in TOPICS:
        matching = [a for a in articles if topic["label"] in a.topics]
        if not matching:
            continue
        recent = sum(1 for a in matching if stamps[a.pmid] >= midpoint)
        previous = sum(1 for a in matching if stamps[a.pmid] < midpoint)
        recent_share = recent / recent_total
        previous_share = previous / previous_total
        if previous_share > 0:
            delta = round(((recent_share - previous_share) / previous_share) * 100)
        else:
            delta = 100 if recent > 1 else 0
        delta = max(-99, min(199, delta))
        share = round(len(matching) / max(len(articles), 1) * 100)
        sparse = len(matching) < MIN_TREND_COUNT or share < MIN_TREND_SHARE
        if sparse:
            signal = "sparse"
        elif delta >= 20 and recent >= MIN_TREND_HALF and previous >= MIN_TREND_HALF:
            signal = "rising"
        elif delta <= -20 and previous >= MIN_TREND_HALF and recent >= MIN_TREND_HALF:
            signal = "cooling"
        else:
            signal = "steady"
        rows.append(Trend(topic["label"], len(matching), previous, recent, delta, share, signal))
    # 편수 순 정렬. 변화율 정렬은 표본이 가장 적은 칸을 맨 위로 올렸다.
    rows.sort(key=lambda t: (-t.count, -t.delta))
    return rows


def keep_on_target_topics(trends: list[Trend]) -> list[Trend]:
    return [t for t in trends if not TOPIC_JOINT.get(t.label) or TOPIC_JOINT[t.label] == TARGET_JOINT]


# ---------------------------------------------------------------------------
# 한국어 조사
# ---------------------------------------------------------------------------

def _final_jong(word: str) -> int:
    if not word:
        return -1
    code = ord(word[-1])
    if code < 0xAC00 or code > 0xD7A3:
        return -1
    return (code - 0xAC00) % 28


def with_particle(word: str, after_consonant: str, after_vowel: str) -> str:
    return word + (after_consonant if _final_jong(word) > 0 else after_vowel)




# ---------------------------------------------------------------------------
# 아이디어
# ---------------------------------------------------------------------------

def _pick_evidence(items: list[Article], limit: int = 2) -> list[dict]:
    ranked = sorted((a for a in items if a.abstract), key=lambda a: a.date, reverse=True)
    return [{"pmid": a.pmid, "title": a.title} for a in ranked[:limit]]


def _clamp(value: float) -> int:
    return max(2, min(5, round(value)))


def _deficit_z(observed: int, size: int, baseline: float) -> float:
    """기준선 대비 부족분의 z값. 양수가 클수록 "나머지 코퍼스보다 유의하게 낮다".

    강도로도 그대로 쓴다. 편수에 비례하던 기존 공식과 달리 √n으로 완만하게 커져,
    큰 주제가 순위를 독식하는 구조적 편향이 줄어든다.
    """
    if size <= 0 or not 0.0 < baseline < 1.0:
        return 0.0
    se = math.sqrt(baseline * (1.0 - baseline) / size)
    return (baseline - observed / size) / se if se > 0 else 0.0


def _baseline(rest: list[Article], predicate) -> float:
    return (sum(1 for a in rest if predicate(a)) / len(rest)) if rest else 0.0


def _cohens_h(observed_rate: float, baseline: float) -> float:
    """두 비율의 효과크기. 부족분이 클수록 양수.

    z는 표본이 커지면 격차가 작아도 커진다(1,000편이면 7%p 차이도 z>5). 실질적인
    격차 크기는 표본에 좌우되지 않는 h로 따로 재야 "통계적으로만 유의한 공백"을
    가려낼 수 있다.
    """
    def phi(p: float) -> float:
        return 2 * math.asin(math.sqrt(max(0.0, min(1.0, p))))
    return phi(baseline) - phi(observed_rate)


def _sufficiency(n: int, h: float) -> str:
    if n >= GAP_N_SOLID and abs(h) >= GAP_H_SOLID:
        return "충분"
    if n >= GAP_N_LIMITED and abs(h) >= GAP_H_LIMITED:
        return "제한적"
    return "부족"


def _half_gap(pool: list[Article], rest: list[Article], predicate, stamps: dict, midpoint: float,
              late: bool) -> dict | None:
    """반기 하나에서 같은 공백을 다시 잰다. 기준선도 그 반기 것으로 다시 잡는다.

    기준선을 전 기간으로 고정하면, 코퍼스 전체가 그 지표를 더 많이 다루기 시작했을 때
    주제의 비중이 그대로인데도 공백이 줄어든 것처럼 보인다.
    """
    def in_half(a: Article) -> bool:
        stamp = stamps.get(a.pmid, float("nan"))
        return stamp >= midpoint if late else stamp < midpoint

    pool_half = [a for a in pool if in_half(a)]
    rest_half = [a for a in rest if in_half(a)]
    if len(pool_half) < GAP_TEMPORAL_MIN_HALF or len(rest_half) < GAP_TEMPORAL_MIN_HALF:
        return None
    ratio = sum(1 for a in pool_half if predicate(a)) / len(pool_half)
    base = sum(1 for a in rest_half if predicate(a)) / len(rest_half)
    return {"n": len(pool_half), "ratio": round(ratio, 4), "baseline": round(base, 4),
            "gap": round(base - ratio, 4)}


def _gap_metrics(pool: list[Article], rest: list[Article], predicate, stamps: dict, midpoint: float) -> dict:
    """공백 하나의 크기·확실성·시간 변화를 한 번에 잰다.

    네 규칙(결과·설계·하위집단·교차)이 모두 "이 묶음이 나머지 코퍼스보다 이 속성을
    덜 가졌는가"라는 같은 형태라서, 지표도 같은 틀로 계산해 화면에서 나란히 비교된다.
    """
    n = len(pool)
    observed = sum(1 for a in pool if predicate(a))
    ratio = observed / n if n else 0.0
    baseline = _baseline(rest, predicate)
    h = _cohens_h(ratio, baseline)
    early = _half_gap(pool, rest, predicate, stamps, midpoint, False)
    late = _half_gap(pool, rest, predicate, stamps, midpoint, True)
    if early and late:
        shift = late["gap"] - early["gap"]
        direction = ("narrowing" if shift <= -GAP_TEMPORAL_SHIFT else
                     "widening" if shift >= GAP_TEMPORAL_SHIFT else "persistent")
    else:
        shift, direction = None, "unknown"
    return {
        "n": n, "observed": observed, "ratio": round(ratio, 4), "baseline": round(baseline, 4),
        "z": round(_deficit_z(observed, n, baseline), 2), "effectSize": round(h, 3),
        "sufficiency": _sufficiency(n, h),
        "temporal": {"direction": direction, "shift": round(shift, 4) if shift is not None else None,
                     "early": early, "late": late},
    }


def generate_ideas(articles: list[Article], trends: list[Trend],
                   date_from: str = "", date_to: str = "") -> list[Idea]:
    """실제 코퍼스에 있는 공백에서 아이디어를 도출한다. 공백이 없으면 아무것도 내지 않는다."""
    articles = [a for a in articles if a.design not in NON_CLINICAL_DESIGNS]
    total = len(articles)
    if not total:
        return []
    # 시간적 근거용 반기 경계. build_trends와 같은 방식으로 잘라 트렌드 표와 어긋나지 않게 한다.
    stamps = {a.pmid: _ts(a.date) for a in articles}
    if date_from and date_to:
        midpoint = _ts(date_from) + ((_ts(date_to) + 86399) - _ts(date_from)) / 2
    else:
        midpoint = float("nan")   # 기간을 모르면 반기 비교를 포기한다(방향 unknown)
    ranked = [t for t in trends if t.signal != "sparse"]
    pools = {t.label: [a for a in articles if t.label in a.topics] for t in ranked}
    subgroup_map = {a.pmid: classify_subgroups(f"{a.title} {a.abstract}") for a in articles}

    # (strength, kind, lead, axis, idea). axis는 하위집단·교차처럼 두 번째 축이 있는 규칙에서
    # 그 축이 목록을 독식하지 않게 막는 용도. 없으면 None.
    candidates: list[tuple[float, str, str, str | None, Idea]] = []

    for trend in ranked:
        pool = pools[trend.label]
        if len(pool) < IDEA_MIN_POOL:
            continue
        growing = trend.signal == "rising"
        gw = 1.6 if growing else 1
        delta_text = f"{'+' if trend.delta > 0 else ''}{trend.delta}%"

        # 이 주제를 다루지 않는 나머지 초록이 비교 기준. 주제 자신을 기준선에 넣으면
        # 큰 주제일수록 자기 자신과 비교하게 되어 공백이 희석된다.
        rest = [a for a in articles if trend.label not in a.topics]

        # 결과 공백: 나머지 코퍼스보다 PROM을 유의하게 덜 다룬다.
        with_prom = [a for a in pool if "PROM·기대치" in a.topics]
        prom_ratio = len(with_prom) / len(pool)
        prom_base = _baseline(rest, lambda a: "PROM·기대치" in a.topics)
        prom_z = _deficit_z(len(with_prom), len(pool), prom_base)
        if trend.label != "PROM·기대치" and prom_z >= GAP_Z and prom_ratio <= prom_base * GAP_MIN_RATIO:
            prom_metrics = _gap_metrics(pool, rest, lambda a: "PROM·기대치" in a.topics, stamps, midpoint)
            candidates.append((prom_z * gw, "outcome", trend.label, None, Idea(
                id=f"outcome-{trend.label}",
                title=f"{trend.label} 연구의 결과가 환자 체감 회복으로 이어지는가?",
                rationale=(f"이 범위에서 {with_particle(trend.label, '은', '는')} {len(pool)}편({trend.share}%)이고 전반기 대비 {delta_text} 변화했습니다. "
                           f"PROM·기대치를 함께 다룬 초록은 {len(with_prom)}편({round(prom_ratio * 100)}%)으로, "
                           f"나머지 문헌의 {round(prom_base * 100)}%보다 {prom_z:.1f}표준편차 낮습니다. "
                           "지표 개선이 환자가 체감하는 회복으로 옮겨가는지는 아직 비어 있습니다."),
                pico=f"{trend.label} 관련 수술·시술을 받은 성인에서, 해당 지표의 개선이 12개월 PROM의 MCID 달성을 예측하는지 평가",
                design="후향 코호트 + 시간순 내부검증 (기존 PROM 추적자료 활용)",
                primaryEndpoint="12개월 질환별 PROM의 MCID 달성률",
                novelty=_clamp(2 + prom_z / 3), feasibility=5,
                evidence=_pick_evidence(pool), tags=[trend.label, "PROM·MCID", "결과지표 공백"],
                metrics=prom_metrics,
            )))

        # 근거수준 공백: 나머지 코퍼스보다 전향 연구 비중이 유의하게 낮다.
        prospective = [a for a in pool if a.design in PROSPECTIVE_DESIGNS]
        p_ratio = len(prospective) / len(pool)
        pro_base = _baseline(rest, lambda a: a.design in PROSPECTIVE_DESIGNS)
        pro_z = _deficit_z(len(prospective), len(pool), pro_base)
        if pro_z >= GAP_Z and p_ratio <= pro_base * GAP_MIN_RATIO:
            pro_metrics = _gap_metrics(pool, rest, lambda a: a.design in PROSPECTIVE_DESIGNS, stamps, midpoint)
            # 설계 공백은 해결책이 명확해(전향 등록) 결과 공백보다 조금 우대한다.
            candidates.append((pro_z * 1.2 * gw, "design", trend.label, None, Idea(
                id=f"design-{trend.label}",
                title=f"{trend.label}의 후향적 결론을 전향적으로 재현할 수 있는가?",
                rationale=(f"{trend.label} {len(pool)}편 중 전향적 연구·무작위시험은 {len(prospective)}편({round(p_ratio * 100)}%)으로, "
                           f"나머지 문헌의 {round(pro_base * 100)}%보다 {pro_z:.1f}표준편차 낮습니다. "
                           "대부분 후향 자료에 기대고 있어 적응증 선택 편향을 배제하지 못합니다. "
                           "단일 기관 전향 등록만으로도 근거 수준을 한 단계 올릴 수 있는 구간입니다."),
                pico=f"{trend.label} 적응증 환자에서, 사전 정의된 프로토콜에 따른 전향 추적이 기존 후향 보고와 같은 결과를 보이는지 검증",
                design="단일·다기관 전향 관찰 등록연구 (사전 등록 권장)",
                primaryEndpoint="사전 정의된 12개월 1차 결과변수의 재현 여부",
                novelty=_clamp(2 + pro_z / 3), feasibility=3,
                evidence=_pick_evidence(pool), tags=[trend.label, "전향 검증", "근거수준 공백"],
                metrics=pro_metrics,
            )))

        # 하위집단 공백: 코퍼스가 충분히 다루는 환자군인데 이 주제는 따로 검증하지 않는다.
        for group in SUBGROUPS:
            label = group["label"]
            if label in trend.label:
                continue
            family = group.get("family")
            if family and _family_share(pool, family) < 0.5:
                continue    # 이 주제는 해당 계열 문헌이 주류가 아니다 → 범주 오류
            base = _baseline(rest, lambda a: label in subgroup_map[a.pmid])
            if base < 0.04:            # 코퍼스 자체가 거의 안 다루면 공백이 아니라 관심 밖이다
                continue
            observed = [a for a in pool if label in subgroup_map[a.pmid]]
            z = _deficit_z(len(observed), len(pool), base)
            ratio = len(observed) / len(pool)
            if z < GAP_Z or ratio > base * GAP_MIN_RATIO:
                continue
            sub_metrics = _gap_metrics(pool, rest, lambda a, g=label: g in subgroup_map[a.pmid], stamps, midpoint)
            candidates.append((z * gw, "subgroup", trend.label, label, Idea(
                id=f"subgroup-{trend.label}-{label}",
                title=f"{trend.label} 연구에서 {with_particle(label, '은', '는')} 따로 검증됐는가?",
                rationale=(f"이 범위 전체에서 {with_particle(label, '을', '를')} 명시적으로 다룬 초록은 {round(base * 100)}%인데, "
                           f"{trend.label} {len(pool)}편 중에서는 {len(observed)}편({round(ratio * 100)}%)뿐입니다"
                           f"(기준선보다 {z:.1f}표준편차 낮음). "
                           f"결과가 갈릴 가능성이 큰 환자군인데 하위군 분석이 비어 있는 구간입니다."),
                pico=f"{label}에 해당하는 무릎 환자에서, {trend.label}에서 확립된 지표·개입이 전체 코호트와 같은 방향의 결과를 보이는지 평가",
                design="기존 코호트의 사전 정의된 하위군 분석 (검정력 확인 후) 또는 해당 환자군 전향 등록",
                primaryEndpoint="전체 코호트 대비 하위군의 12개월 1차 결과변수 차이 (교호작용 검정)",
                novelty=_clamp(3 + z / 3), feasibility=4,
                evidence=_pick_evidence(observed or pool), tags=[trend.label, label, "하위집단 공백"],
                metrics=sub_metrics,
            )))

    # 교차 공백: 규모 대비 함께 다뤄지지 않는 두 주제.
    for i in range(len(ranked)):
        for j in range(i + 1, len(ranked)):
            first, second = ranked[i], ranked[j]
            if first.signal != "rising" and second.signal != "rising":
                continue
            fj, sj = TOPIC_JOINT.get(first.label), TOPIC_JOINT.get(second.label)
            if fj and sj and fj != sj:
                continue
            pool_first, pool_second = pools[first.label], pools[second.label]
            if len(pool_first) < IDEA_MIN_POOL or len(pool_second) < IDEA_MIN_POOL:
                continue
            observed = [a for a in pool_first if second.label in a.topics]
            expected = len(pool_first) * len(pool_second) / total
            if expected < 3 or len(observed) >= expected * GAP_RATIO:
                continue
            lead = first.label if first.signal == "rising" else second.label
            # 교차 공백도 같은 틀로 잰다: 첫 주제 묶음이 "둘째 주제를 함께 다루는 비율"을
            # 첫 주제 밖 문헌의 같은 비율과 비교한다.
            cross_rest = [a for a in articles if first.label not in a.topics]
            cross_metrics = _gap_metrics(pool_first, cross_rest,
                                         lambda a, t=second.label: t in a.topics, stamps, midpoint)
            candidates.append(((expected - len(observed)) * 1.4, "intersection", lead, second.label, Idea(
                id=f"intersection-{first.label}-{second.label}",
                title=f"{with_particle(first.label, '과', '와')} {with_particle(second.label, '을', '를')} 함께 보면 무엇이 달라지는가?",
                rationale=(f"{first.label} {len(pool_first)}편, {second.label} {len(pool_second)}편이 각각 축적되어 있는데 둘을 함께 다룬 초록은 "
                           f"{len(observed)}편입니다(두 주제 크기대로면 {round(expected)}편). 각자 성숙한 두 흐름이 아직 만나지 않은 지점이라, "
                           "교차 지점에서 새 질문이 나오기 쉽습니다."),
                pico=f"{first.label} 대상 환자에서, {second.label} 관련 인자를 함께 측정했을 때 결과 예측이 개선되는지 평가",
                design="기존 두 코호트의 조화 분석 또는 전향 병행 측정",
                primaryEndpoint="두 축을 함께 넣은 모형의 예측력 개선분",
                novelty=_clamp(4 + (expected - len(observed)) / max(expected, 1)), feasibility=3,
                evidence=_pick_evidence(pool_first, 1) + _pick_evidence(pool_second, 1),
                tags=[first.label, second.label, "교차 공백"],
                metrics=cross_metrics,
            )))

    # 분산: 한쪽 종류나 한 주제가 목록을 독식하지 않도록 상한을 둔다.
    chosen: list[Idea] = []
    kind_used: dict[str, int] = {}
    lead_used: dict[str, int] = {}
    axis_used: dict[str, int] = {}
    for strength, kind, lead, axis, idea in sorted(candidates, key=lambda c: -c[0]):
        if kind_used.get(kind, 0) >= IDEA_PER_KIND or lead_used.get(lead, 0) >= IDEA_PER_LEAD:
            continue
        if axis and axis_used.get(axis, 0) >= IDEA_PER_LEAD:
            continue
        chosen.append(idea)
        kind_used[kind] = kind_used.get(kind, 0) + 1
        lead_used[lead] = lead_used.get(lead, 0) + 1
        if axis:
            axis_used[axis] = axis_used.get(axis, 0) + 1
        if len(chosen) == IDEA_MAX:
            break
    return chosen


def scoped_ideas(articles: list[Article], trends: list[Trend],
                 date_from: str = "", date_to: str = "") -> list[Idea] | None:
    ideas = generate_ideas(articles, trends, date_from, date_to)
    return ideas if len(ideas) >= MIN_IDEAS_FOR_SCOPE else None


# ---------------------------------------------------------------------------
# 수집
# ---------------------------------------------------------------------------

def _valid_date(value) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _even_sample(ids: list[str], target: int) -> list[str]:
    if len(ids) <= target:
        return ids
    return [ids[round(i * (len(ids) - 1) / (target - 1))] for i in range(target)]


def _esearch_journal(key: str, term: str, credentials: NcbiCredentials, throttle: float) -> tuple[list[str], int]:
    """한 저널의 UID 전부를 페이징으로 수집. ESearch는 200으로 ERROR나 빈 목록을 주기도 하므로 재시도한다."""
    for attempt in range(1, ESEARCH_ATTEMPTS + 1):
        ids: list[str] = []
        count = 0
        complete = True
        retstart = 0
        while True:
            body = fetch_ncbi_text(
                f"{EUTILS}/esearch.fcgi",
                params={"db": "pubmed", "term": term, "retmode": "json", "retmax": ESEARCH_PAGE_SIZE,
                        "retstart": retstart, "sort": "pub date", **ncbi_params(credentials)},
                headers={"accept": "application/json"}, label="PubMed 검색 오류",
            )
            result = json.loads(body).get("esearchresult")
            if not result or result.get("ERROR"):
                complete = False
                break
            page = result.get("idlist") or []
            count = int(result.get("count", len(page)))
            ids.extend(page)
            if len(ids) >= count:
                break
            if not page:
                complete = False
                break
            retstart += ESEARCH_PAGE_SIZE
            time.sleep(throttle)
        if complete and len(ids) >= count:
            return ids, count
        if attempt >= ESEARCH_ATTEMPTS:
            raise RuntimeError(f"PubMed 검색 오류 ({JOURNALS[key]['label']}: {count}건 중 {len(ids)}건만 수신)")
        time.sleep(0.5 * attempt)
    return [], 0


def _efetch_batch(batch: list[str], credentials: NcbiCredentials, unknown: set[str]) -> list[Article]:
    parsed: list[Article] = []
    for attempt in range(1, EFETCH_PARSE_ATTEMPTS + 1):
        xml = fetch_ncbi_text(
            f"{EUTILS}/efetch.fcgi", params=ncbi_params(credentials),
            data={"db": "pubmed", "id": ",".join(batch), "retmode": "xml"},
            headers={"accept": "application/xml"}, label="PubMed 초록 수집 오류",
        )
        parsed = parse_articles(xml, unknown)
        if len(parsed) >= len(batch) * EFETCH_MIN_YIELD:
            break
        if attempt < EFETCH_PARSE_ATTEMPTS:
            time.sleep(0.4 * attempt)
    if len(parsed) < len(batch) * EFETCH_MIN_YIELD:
        raise RuntimeError(f"PubMed 초록 수집 오류 ({len(batch)}건 요청, {len(parsed)}건 수신)")
    return parsed


def run_analysis(journals: list[str], date_from: str, date_to: str, focus: str = "",
                 credentials: NcbiCredentials | None = None, progress=None) -> dict:
    """전체 파이프라인. progress(fraction, message) 콜백은 선택."""
    credentials = credentials or NcbiCredentials()
    journals = [k for k in JOURNAL_ORDER if k in (journals or [])]
    if not journals:
        raise AnalysisError("분석할 저널을 선택해 주세요.", 400)
    if not _valid_date(date_from) or not _valid_date(date_to):
        raise AnalysisError("분석 기간을 확인해 주세요.", 400)
    range_days = (_ts(date_to) - _ts(date_from)) / 86400
    if range_days < 0 or range_days > 730:
        raise AnalysisError("분석 기간은 최대 24개월까지 설정할 수 있습니다.", 400)

    def report(fraction: float, message: str):
        if progress:
            progress(min(max(fraction, 0.0), 1.0), message)

    throttle = 0.12 if credentials.api_key else 0.36
    focus = re.sub(r"\s+", " ", re.sub(r'["\[\]{}]', " ", focus or "")).strip()[:120]
    date_query = f'("{date_from.replace("-", "/")}"[Date - Publication] : "{date_to.replace("-", "/")}"[Date - Publication])'
    focus_query = f" AND ({focus}[Title/Abstract])" if focus else ""

    total_available = 0
    ids: list[str] = []
    for index, key in enumerate(journals):
        report(0.05 + 0.25 * index / len(journals), f"PubMed 검색 중 — {JOURNALS[key]['short']}")
        term = f"{JOURNALS[key]['query']} AND {date_query}{focus_query}"
        journal_ids, count = _esearch_journal(key, term, credentials, throttle)
        total_available += count
        ids.extend(journal_ids)
        if index < len(journals) - 1:
            time.sleep(throttle)

    collected = len(ids)
    selected = _even_sample(ids, MAX_TOTAL_ARTICLES) if collected > MAX_TOTAL_ARTICLES else ids
    if not selected:
        raise AnalysisError("선택한 조건에 해당하는 PubMed 문헌이 없습니다. 검색어 또는 기간을 넓혀 주세요.", 404)

    batches = [selected[i:i + EFETCH_BATCH_SIZE] for i in range(0, len(selected), EFETCH_BATCH_SIZE)]
    wave = EFETCH_CONCURRENCY if credentials.api_key else 1
    unknown: set[str] = set()
    parsed_batches: list[list[Article]] = [[] for _ in batches]
    for start in range(0, len(batches), wave):
        report(0.3 + 0.5 * start / len(batches), f"초록 수집 중 — {min(start + wave, len(batches))}/{len(batches)} 묶음")
        chunk = batches[start:start + wave]
        with ThreadPoolExecutor(max_workers=wave) as pool:
            results = list(pool.map(lambda b: _efetch_batch(b, credentials, unknown), chunk))
        for offset, parsed in enumerate(results):
            parsed_batches[start + offset] = parsed
        if start + wave < len(batches):
            time.sleep(throttle * wave)

    report(0.85, "주제 분류 및 트렌드 계산 중")
    all_articles = sorted((a for b in parsed_batches for a in b), key=lambda a: a.date, reverse=True)
    with_abstract = [a for a in all_articles if a.abstract]
    identified = [a for a in with_abstract if a.joint != "기타·다관절"]
    excluded_multi = len(with_abstract) - len(identified)
    articles = [a for a in identified if a.joint == TARGET_JOINT]
    excluded_other = len(identified) - len(articles)

    trends = keep_on_target_topics(build_trends(articles, date_from, date_to))
    trends_by_journal = {k: keep_on_target_topics(build_trends([a for a in articles if a.journalKey == k], date_from, date_to))
                         for k in journals}
    ideas = generate_ideas(articles, trends, date_from, date_to)

    selected_families = [f for f in FAMILY_ORDER if any(JOURNALS[k]["family"] == f for k in journals)]

    def family_articles(fam):
        return [a for a in articles if JOURNALS[a.journalKey]["family"] == fam]

    families = [{"key": f, "label": FAMILIES[f]["label"], "short": FAMILIES[f]["short"],
                 "count": len(family_articles(f)), "journals": [k for k in journals if JOURNALS[k]["family"] == f]}
                for f in selected_families]
    split_families = [f for f in selected_families if len([k for k in journals if JOURNALS[k]["family"] == f]) > 1]
    trends_by_family = {f: keep_on_target_topics(build_trends(family_articles(f), date_from, date_to)) for f in split_families}
    ideas_by_family = {}
    for f in split_families:
        scoped = scoped_ideas(family_articles(f), trends_by_family.get(f, []), date_from, date_to)
        if scoped:
            ideas_by_family[f] = scoped
    ideas_by_journal = {}
    for k in journals:
        scoped = scoped_ideas([a for a in articles if a.journalKey == k], trends_by_journal.get(k, []), date_from, date_to)
        if scoped:
            ideas_by_journal[k] = scoped

    def preview(a: Article) -> dict:
        d = asdict(a)
        if len(a.abstract) > ABSTRACT_PREVIEW_CHARS:
            d["abstract"] = a.abstract[:ABSTRACT_PREVIEW_CHARS].rstrip() + "…"
        return d

    report(1.0, "완료")
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "apiKeyActive": bool(credentials.api_key),
        "collected": collected,
        "capped": collected > MAX_TOTAL_ARTICLES,
        "cap": MAX_TOTAL_ARTICLES,
        "unknownJournals": sorted(unknown),
        "dateFrom": date_from,
        "dateTo": date_to,
        "query": focus,
        "totalAvailable": total_available,
        "analyzed": len(articles),
        "withAbstract": len(with_abstract),
        "excludedMultiJoint": excluded_multi,
        "excludedOtherJoints": excluded_other,
        "abstractCoverage": round(len(with_abstract) / max(len(all_articles), 1) * 100),
        "journals": [{"key": k, "label": JOURNALS[k]["label"], "count": sum(1 for a in articles if a.journalKey == k)} for k in journals],
        "trends": [asdict(t) for t in trends],
        "trendsByJournal": {k: [asdict(t) for t in v] for k, v in trends_by_journal.items()},
        "trendsByFamily": {k: [asdict(t) for t in v] for k, v in trends_by_family.items()},
        "families": families,
        "ideas": [asdict(i) for i in ideas],
        "ideasByJournal": {k: [asdict(i) for i in v] for k, v in ideas_by_journal.items()},
        "ideasByFamily": {k: [asdict(i) for i in v] for k, v in ideas_by_family.items()},
        "articles": [preview(a) for a in articles],
    }
