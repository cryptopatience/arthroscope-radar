"""선행연구 검증 코퍼스.

트렌드 탐지는 최근 12개월로 한다. 그 창으로는 "무엇이 늘고 있나"와 "지금 무엇이
비어 있나"를 볼 수 있지만 **독창성은 판정할 수 없다**. 3년 전에 같은 연구가 이미
잘 수행됐다면 그것은 공백이 아니라 우리 창이 짧은 것이다.

그래서 후보마다 PubMed를 다시 검색한다. 이번에는 10개 저널로 제한하지 않고
기간도 길게 잡는다. 목적이 다르기 때문이다.

- 탐지 코퍼스: 최근 12개월 · 10개 저널 → 신호와 공백
- 검증 코퍼스: 최근 10년 · PubMed 전체 → 이미 답해졌는가

일치 판정은 규칙으로 한다. 여기서 LLM에 "이게 같은 연구인가"를 물으면, 앞에서
확신도를 버린 이유와 같은 문제가 다시 생긴다 — 재현되지 않는 점수가 독창성 축을
좌우하게 된다.
"""
from __future__ import annotations

import json
import time
from datetime import date

from . import config
from .analysis import (JOURNALS, SUBGROUPS, TOPICS, _count_term, _matches_term,
                       classify_facets, classify_subgroups)
from .ncbi import EUTILS, NcbiCredentials, PubmedRecord, fetch_abstracts, fetch_ncbi_text, ncbi_params
from .vocabulary import (ACCURACY_TERMS, DIRECT_COMPARISON_TERMS, EXTERNAL_VALIDATION_TERMS,
                         LONGTERM_TERMS, PATIENT_OUTCOME_TERMS, PROM_CLINICAL_INTERPRETATION,
                         PROM_INSTRUMENTS)

# 검색어에 넣을 용어 수 상한. 전부 넣으면 질의가 수천 자가 되고 PubMed가 느려진다.
MAX_QUERY_TERMS = 12
PROSPECTIVE_TERMS = ["prospective", "randomized controlled trial", "randomised controlled trial",
                     "randomized", "prospectively enrolled"]

# gapId 접두어 → 그 공백이 묻는 것을 PubMed에서 찾을 용어
GAP_QUERY_TERMS = {
    "prom_measurement": PROM_INSTRUMENTS,
    "prom_interpretation": PROM_CLINICAL_INTERPRETATION,
    "prospective_design": PROSPECTIVE_TERMS,
    "direct_comparison": DIRECT_COMPARISON_TERMS,
    "longterm_followup": LONGTERM_TERMS,
    "external_validation": EXTERNAL_VALIDATION_TERMS,
    "clinical_utility": PATIENT_OUTCOME_TERMS + ACCURACY_TERMS,
}
# gapId 접두어 → 그 공백을 다뤘다고 볼 facet
GAP_FACET = {
    "prom_measurement": "prom",
    "prom_interpretation": "prom_interpretation",
    "direct_comparison": "direct_comparison",
    "longterm_followup": "longterm",
    "external_validation": "external_validation",
    "clinical_utility": "patient_outcome",
}

_TOPIC_TERMS = {t["label"]: t["terms"] for t in TOPICS}
_SUBGROUP_TERMS = {g["label"]: g["terms"] for g in SUBGROUPS}


def _or_clause(terms: list[str]) -> str:
    picked = [t for t in terms[:MAX_QUERY_TERMS] if t.strip()]
    return "(" + " OR ".join(f'"{t}"[tiab]' for t in picked) + ")" if picked else ""


def _gap_terms(gap_id: str) -> list[str]:
    if gap_id in GAP_QUERY_TERMS:
        return GAP_QUERY_TERMS[gap_id]
    if gap_id.startswith("subgroup_"):
        return _SUBGROUP_TERMS.get(gap_id[len("subgroup_"):], [])
    if gap_id.startswith("cross_"):
        return _TOPIC_TERMS.get(gap_id[len("cross_"):], [])
    return []


def build_query(idea: dict, years: int = 0, today: date | None = None) -> str:
    """후보 하나를 PubMed 전체에서 다시 찾는 질의.

    저널 제한을 걸지 않는다. 우리 10개 저널 밖에서 이미 답해졌는지가 정확히
    알고 싶은 것이다.
    """
    years = years or config.PRIOR_ART_YEARS
    today = today or date.today()
    cluster = _or_clause(_TOPIC_TERMS.get(idea.get("clusterId", ""), []))
    gap = _or_clause(_gap_terms(idea.get("gapId", "")))
    parts = ['("knee"[tiab] OR "knees"[tiab])']
    if cluster:
        parts.append(cluster)
    if gap:
        parts.append(gap)
    parts.append(f'("{today.year - years}"[dp] : "{today.year}"[dp])')
    return " AND ".join(parts)


def _esearch(term: str, credentials: NcbiCredentials, retmax: int) -> tuple[list[str], int]:
    body = fetch_ncbi_text(
        f"{EUTILS}/esearch.fcgi",
        params={"db": "pubmed", "term": term, "retmode": "json", "retmax": retmax,
                "sort": "relevance", **ncbi_params(credentials)},
        headers={"accept": "application/json"}, label="선행연구 검색 오류",
    )
    result = json.loads(body).get("esearchresult") or {}
    if result.get("ERROR"):
        raise RuntimeError(f"선행연구 검색 오류 ({result['ERROR']})")
    return list(result.get("idlist") or []), int(result.get("count", 0))


def _central(title: str, text: str, terms: list[str]) -> bool:
    """이 논문의 주제인가, 지나가는 언급인가.

    ESearch가 이미 클러스터·공백 용어로 AND 검색하므로, 같은 조건을 다시 확인하면
    받아온 논문이 전부 통과한다(실제로 39편 중 39편이 통과했다). 독창성 점수가
    무력화되므로 검색보다 엄격한 기준이 필요하다: 제목에 나오거나, 초록에서
    두 번 이상 언급돼야 그 논문의 주제로 본다.
    """
    if any(_matches_term(title, t) for t in terms):
        return True
    return sum(_count_term(text, t) for t in terms) >= 2


def classify_match(record: PubmedRecord, idea: dict) -> str:
    """선행연구를 세 단계로 나눈다.

    단어 반복 횟수는 관련성의 근사치일 뿐이다. 개념이 몇 개나 겹치는지로 나누면
    "질문에 직접 답한 연구"와 "주제만 같은 연구"를 구분할 수 있다.

    direct     대상·공백지표가 모두 그 논문의 주제이고, 비교군/대상군 조건까지 맞는다
    adjacent   둘 중 하나만 주제이거나, 공백지표는 언급되지만 중심은 아니다
    background 검색에는 걸렸으나 대상 주제만 같다
    """
    title = (record.title or "").lower()
    text = f"{record.title} {record.abstract}".lower()
    gap_id = idea.get("gapId", "")

    cluster_terms = _TOPIC_TERMS.get(idea.get("clusterId", ""), [])
    cluster_central = _central(title, text, cluster_terms) if cluster_terms else True
    if not cluster_central and not any(_matches_term(text, t) for t in cluster_terms):
        return "background"

    gap_terms = _gap_terms(gap_id)
    gap_present = any(_matches_term(text, t) for t in gap_terms) if gap_terms else True
    gap_central = _central(title, text, gap_terms) if gap_terms else True
    facet = GAP_FACET.get(gap_id)
    if facet and facet not in classify_facets(text):
        gap_present = gap_central = False

    if not gap_present:
        return "background"
    if not (cluster_central and gap_central):
        return "adjacent"

    # 대상군·비교군 조건이 붙는 공백은 그것까지 맞아야 direct다.
    if gap_id.startswith("subgroup_") and gap_id[len("subgroup_"):] not in classify_subgroups(text):
        return "adjacent"
    if gap_id == "prospective_design" and not any(
            t in text for t in ("prospective", "randomized", "randomised")):
        return "adjacent"
    if gap_id == "direct_comparison" and not any(
            t in text for t in ("versus", "compared with", "compared to", "randomized", "randomised")):
        return "adjacent"
    return "direct"


def check(idea: dict, credentials: NcbiCredentials, years: int = 0,
          throttle: float = 0.15) -> dict:
    """후보 하나의 선행연구를 확인한다.

    matchCount가 독창성 점수의 근거다. 0이면 정말 아무도 안 했다는 뜻이 아니라
    "이 질의로는 안 잡힌다"는 뜻이므로, 질의와 검색 건수를 함께 남겨 사람이
    확인할 수 있게 한다.
    """
    query = build_query(idea, years)
    try:
        pmids, total = _esearch(query, credentials, config.PRIOR_ART_MAX_HITS)
    except Exception as error:
        return {"query": query, "error": str(error) or "선행연구 검색 실패"}
    time.sleep(throttle)
    if not pmids:
        # 0건을 "아무도 안 했다"로 읽으면 안 된다. 검색식이 좁아 실패했을 가능성이
        # 더 크므로 matchCount를 주지 않고 미측정으로 남긴다.
        return {"query": query, "total": 0, "examined": 0, "matches": [],
                "note": "이 질의로는 선행연구가 한 건도 잡히지 않았습니다. 검색식 실패 가능성이 있어 "
                        "독창성을 측정하지 않았습니다."}
    try:
        records = fetch_abstracts(pmids, credentials)
    except Exception as error:
        return {"query": query, "total": total, "error": str(error) or "선행연구 초록 수집 실패"}

    # 우리 코퍼스에 이미 있는 논문은 "선행"이 아니다. 최근 12개월 것을 빼면
    # 남는 것이 진짜 이전 연구다.
    seeds = {e.get("pmid") for e in idea.get("evidence", [])}
    tiers = {"direct": [], "adjacent": [], "background": []}
    for record in records:
        if record.pmid in seeds:
            continue
        tiers[classify_match(record, idea)].append(record)

    def brief(records_):
        return [{"pmid": r.pmid, "year": r.year, "journal": r.journal, "title": r.title}
                for r in sorted(records_, key=lambda r: r.year or "", reverse=True)[:5]]

    direct = tiers["direct"]
    return {
        "query": query,
        "total": total,                 # 질의에 걸린 전체 건수 (범위 감각용)
        "examined": len(records),       # 실제로 초록까지 읽은 수
        "matchCount": len(direct),      # 질문에 직접 답한 수 → 독창성 점수의 근거
        "adjacentCount": len(tiers["adjacent"]),
        "backgroundCount": len(tiers["background"]),
        "matches": brief(direct),
        "adjacent": brief(tiers["adjacent"]),
        "ownJournals": sum(1 for r in direct if any(
            j["short"].lower() in (r.journal or "").lower() for j in JOURNALS.values())),
        "years": years or config.PRIOR_ART_YEARS,
    }
