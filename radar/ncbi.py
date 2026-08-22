"""NCBI E-utilities 호출 유틸리티 (lib/ncbi.ts 포팅)."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

import requests

TOOL_NAME = "arthroscope_research_radar"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

# 한 번의 분석에 E-utilities 호출이 20회 안팎이므로, 일시적인 502 하나가
# 전체 실행을 날리지 않도록 재시도한다. 400도 의도적으로 재시도 대상:
# EFetch에는 ESearch가 방금 돌려준 PMID만 넘어가므로 400은 보통 NCBI 부하다.
NCBI_RETRY_STATUSES = {400, 429, 500, 502, 503, 504}
NCBI_MAX_ATTEMPTS = 3
TIMEOUT = 60


@dataclass
class NcbiCredentials:
    api_key: str = ""
    email: str = ""


def ncbi_params(credentials: NcbiCredentials) -> dict:
    params = {"tool": TOOL_NAME}
    if credentials.email:
        params["email"] = credentials.email
    if credentials.api_key:
        params["api_key"] = credentials.api_key
    return params


def fetch_ncbi_text(url: str, *, params: dict | None = None, data: dict | None = None,
                    headers: dict | None = None, label: str = "NCBI 오류") -> str:
    """응답 본문을 읽는 것까지가 한 번의 시도. 중간에 끊긴 연결은 .text에서 실패한다."""
    last_failure = "네트워크 오류"
    for attempt in range(1, NCBI_MAX_ATTEMPTS + 1):
        retryable = True
        try:
            if data is not None:
                response = requests.post(url, params=params, data=data, headers=headers, timeout=TIMEOUT)
            else:
                response = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
            if response.ok:
                return response.text
            retryable = response.status_code in NCBI_RETRY_STATUSES
            last_failure = str(response.status_code)
        except requests.RequestException as error:
            last_failure = str(error) or "네트워크 오류"
        if not retryable:
            break
        if attempt < NCBI_MAX_ATTEMPTS:
            time.sleep(0.4 * attempt)
    raise RuntimeError(f"{label} ({last_failure})")


_ENTITY_DEC = re.compile(r"&#(\d+);")
_ENTITY_HEX = re.compile(r"&#x([0-9a-f]+);", re.I)


def decode_xml(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    value = (value.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
             .replace("&quot;", '"').replace("&apos;", "'"))
    value = _ENTITY_HEX.sub(lambda m: chr(int(m.group(1), 16)), value)
    value = _ENTITY_DEC.sub(lambda m: chr(int(m.group(1))), value)
    return re.sub(r"\s+", " ", value).strip()


@dataclass
class PubmedRecord:
    pmid: str
    title: str
    abstract: str
    journal: str
    year: str


def fetch_abstracts(pmids: list[str], credentials: NcbiCredentials) -> list[PubmedRecord]:
    """특정 PMID 집합의 전체 초록. 분석 결과는 초록을 잘라서 보내므로 전문이 필요하면 다시 받아온다."""
    if not pmids:
        return []
    xml = fetch_ncbi_text(
        f"{EUTILS}/efetch.fcgi",
        params=ncbi_params(credentials),
        data={"db": "pubmed", "id": ",".join(pmids), "retmode": "xml"},
        headers={"accept": "application/xml"},
        label="PubMed 초록 수집 오류",
    )
    records: list[PubmedRecord] = []
    for block in xml.split("<PubmedArticle>")[1:]:
        pmid_match = re.search(r"<PMID[^>]*>(\d+)<", block)
        if not pmid_match:
            continue
        title_match = re.search(r"<ArticleTitle>([\s\S]*?)</ArticleTitle>", block)
        title = decode_xml(title_match.group(1)) if title_match else ""
        parts = []
        for attrs, body in re.findall(r"<AbstractText([^>]*)>([\s\S]*?)</AbstractText>", block):
            label = re.search(r'Label="([^"]+)"', attrs, re.I)
            text = decode_xml(body)
            parts.append(f"{label.group(1)}: {text}" if label else text)
        abstract = " ".join(parts)
        journal_match = re.search(r"<ISOAbbreviation>([\s\S]*?)</ISOAbbreviation>", block)
        journal = decode_xml(journal_match.group(1)) if journal_match else ""
        year_match = re.search(r"<PubDate>[\s\S]*?<Year>(\d{4})</Year>", block)
        year = year_match.group(1) if year_match else ""
        if abstract:
            records.append(PubmedRecord(pmid_match.group(1), title, abstract, journal, year))
    return records
