"""ClinicalTrials.gov 무릎 임상시험 레이더 — 수집과 변동 감지.

논문 아이디어 파이프라인과는 완전히 독립이다. 아이디어 생성·판정·선정에 아무
영향을 주지 않는다. 하는 일은 두 가지뿐이다.

1. 무릎 인공관절(관절성형)과 무릎 관절경·스포츠의학 관련 임상시험을 모은다
2. 지난 수집과 비교해 무엇이 바뀌었는지(신규 등록·상태 변경·결과 게시·완료일
   이동)를 기록한다

"상태 변경"이 이 레이더의 핵심이다. Recruiting → Completed 전환은 보통 1~2년 안에
논문이 나온다는 신호이고, 결과 게시(hasResults)는 이미 데이터가 공개됐다는 뜻이다.
같은 질문을 준비하는 사람에게는 둘 다 지금 알아야 하는 정보다.

API는 ClinicalTrials.gov v2 (무료 · 키 불필요). PROSPERO는 공식 API가 없어
여기 넣지 않는다 — 스크래핑으로 흉내 내면 조용히 깨지는 코드가 된다.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

API = "https://clinicaltrials.gov/api/v2/studies"
TRIALS_PATH = Path("data/trials.json")
PAGE_SIZE = 200
# 변동 기록을 몇 번의 수집분까지 남길지. 무한히 쌓으면 파일이 본문보다 커진다.
CHANGES_KEEP = 30
# 한 종류의 변동을 몇 건까지 본문으로 남길지. 검색어를 바꾼 날처럼 수천 건이
# 한꺼번에 "신규"가 되는 경우가 있는데, 그것까지 다 적으면 기록이 본문만큼 커진다.
CHANGE_LIST_MAX = 200
# 이 상태들만 "지켜볼 가치가 있는" 시험으로 본다. 철회·중단된 시험은 목록에는
# 남기되 요약 수치에서는 뺀다 — 경쟁 시험이 아니기 때문이다.
ACTIVE_STATUSES = {"RECRUITING", "NOT_YET_RECRUITING", "ACTIVE_NOT_RECRUITING", "ENROLLING_BY_INVITATION"}
STATUS_LABEL = {
    "RECRUITING": "모집 중", "NOT_YET_RECRUITING": "모집 예정",
    "ACTIVE_NOT_RECRUITING": "진행 중 (모집 종료)", "ENROLLING_BY_INVITATION": "초대 모집",
    "COMPLETED": "완료", "TERMINATED": "중단", "WITHDRAWN": "철회",
    "SUSPENDED": "일시 중지", "UNKNOWN": "상태 미상",
}

# 두 계열의 검색어. 앱의 두 계열 구분(관절성형 vs 관절경·스포츠)과 같은 축이다.
# Essie 문법의 고급 연산자는 피하고 단순 불리언만 쓴다 — 검색식이 복잡할수록
# API 개정 때 조용히 0건이 되는 위험이 크다.
FAMILY_QUERIES = {
    "arthroplasty": 'knee AND (arthroplasty OR "knee replacement" OR "total knee" OR "unicompartmental knee")',
    "arthroscopy": ('knee AND (arthroscopy OR "anterior cruciate ligament" OR meniscus OR meniscal '
                    'OR "cartilage repair" OR "return to sport")'),
}
FAMILY_LABEL = {"arthroplasty": "인공관절", "arthroscopy": "관절경·스포츠"}

# 제목·질환명에 무릎이 명시됐는지. 검색은 전체 필드를 훑기 때문에 무릎과 무관한
# 시험이 15%쯤 섞인다(뇌성마비 보행검사, 견봉쇄골 탈구 등). 그렇다고 이 정규식으로
# 걸러 버리면 진짜 무릎 시험도 함께 날아간다 — "Tibial Nerve Versus Sciatic Nerve
# Block", "Factor Xa Inhibitor ... VTE 예방" 같은 TKA 시험은 제목에 knee가 없다.
# 그래서 버리지 않고 표시만 해 두고, 걸러낼지는 화면에서 사람이 정한다.
KNEE_PATTERN = re.compile(
    r"\b(knee|tka|uka|tkr|acl|meniscus|meniscal|patell\w*|gonarthro\w*|cruciate)\b", re.I)

FIELDS = ",".join([
    "NCTId", "BriefTitle", "OverallStatus", "StudyType", "Phase",
    "EnrollmentCount", "Condition", "LeadSponsorName",
    "StartDate", "PrimaryCompletionDate", "LastUpdatePostDate", "HasResults",
])


def _get(node: dict, *path, default=None):
    for key in path:
        node = (node or {}).get(key)
        if node is None:
            return default
    return node


def _flatten(study: dict) -> dict | None:
    """v2 응답의 중첩 구조를 평평한 dict 하나로. NCT 번호가 없으면 버린다.

    저장하는 필드는 화면과 변동 감지가 실제로 쓰는 것으로 제한한다. 응답에 오는
    것을 다 담으면 레코드당 757바이트가 되어 전체 5,800건이면 4MB가 넘고, 이 파일은
    매일 갱신되어 저장소에 커밋된다. 지금 구성은 레코드당 300바이트 수준이다.
    """
    protocol = study.get("protocolSection") or {}
    nct = _get(protocol, "identificationModule", "nctId")
    if not nct:
        return None
    status = _get(protocol, "statusModule", default={})
    design = _get(protocol, "designModule", default={})
    title = _get(protocol, "identificationModule", "briefTitle", default="")
    conditions = _get(protocol, "conditionsModule", "conditions", default=[]) or []
    return {
        "nctId": nct,
        "title": title,
        "status": _get(status, "overallStatus", default="UNKNOWN"),
        "studyType": _get(design, "studyType", default=""),
        "phases": _get(design, "phases", default=[]) or [],
        "enrollment": _get(design, "enrollmentInfo", "count"),
        "sponsor": _get(protocol, "sponsorCollaboratorsModule", "leadSponsor", "name", default=""),
        "startDate": _get(status, "startDateStruct", "date", default=""),
        "primaryCompletionDate": _get(status, "primaryCompletionDateStruct", "date", default=""),
        "lastUpdated": _get(status, "lastUpdatePostDateStruct", "date", default=""),
        "hasResults": bool(study.get("hasResults")),
        # 무릎이 제목·질환명에 명시됐는가. 화면 필터의 재료다(위 KNEE_PATTERN 주석 참고).
        "kneeExplicit": bool(KNEE_PATTERN.search(f"{title} {' '.join(conditions)}")),
    }


def trial_url(nct_id: str) -> str:
    """NCT 번호에서 만들면 되는 값이라 저장하지 않는다."""
    return f"https://clinicaltrials.gov/study/{nct_id}"


def _fetch_family(query: str, throttle: float = 0.5) -> list[dict]:
    """한 계열의 시험 전부를 페이징으로 수집한다."""
    out: list[dict] = []
    token = ""
    while True:
        params = {"query.term": query, "pageSize": PAGE_SIZE, "fields": FIELDS}
        if token:
            params["pageToken"] = token
        response = requests.get(API, params=params, timeout=60,
                                headers={"user-agent": "arthroscope-research-radar"})
        if not response.ok:
            raise RuntimeError(f"ClinicalTrials.gov 호출 실패 (HTTP {response.status_code})")
        payload = response.json()
        for study in payload.get("studies") or []:
            trial = _flatten(study)
            if trial:
                out.append(trial)
        token = payload.get("nextPageToken") or ""
        if not token:
            return out
        time.sleep(throttle)


def collect() -> dict:
    """두 계열을 수집해 NCT 번호로 병합한다. 양쪽에 다 걸리면 families가 둘 다 붙는다.

    (예: 관절경 수술 실패 후 인공관절 전환을 다루는 시험은 양쪽 모두의 관심사다)
    """
    merged: dict[str, dict] = {}
    for family, query in FAMILY_QUERIES.items():
        for trial in _fetch_family(query):
            existing = merged.get(trial["nctId"])
            if existing:
                if family not in existing["families"]:
                    existing["families"].append(family)
            else:
                merged[trial["nctId"]] = {**trial, "families": [family]}
    return {"fetchedAt": datetime.now(timezone.utc).isoformat(), "trials": merged}


def diff(old_trials: dict, new_trials: dict) -> dict:
    """두 수집분의 차이. 각 항목을 화면에 그대로 보여줄 수 있는 형태로 만든다."""
    changes = {"new": [], "statusChanged": [], "resultsPosted": [], "completionMoved": [], "gone": 0}
    for nct, trial in new_trials.items():
        before = old_trials.get(nct)
        if before is None:
            changes["new"].append({"nctId": nct, "title": trial["title"],
                                   "status": trial["status"], "families": trial["families"]})
            continue
        if before.get("status") != trial["status"]:
            changes["statusChanged"].append({
                "nctId": nct, "title": trial["title"], "families": trial["families"],
                "from": before.get("status", "UNKNOWN"), "to": trial["status"]})
        if trial["hasResults"] and not before.get("hasResults"):
            changes["resultsPosted"].append({"nctId": nct, "title": trial["title"],
                                             "families": trial["families"]})
        if (before.get("primaryCompletionDate") and trial["primaryCompletionDate"]
                and before["primaryCompletionDate"] != trial["primaryCompletionDate"]):
            changes["completionMoved"].append({
                "nctId": nct, "title": trial["title"], "families": trial["families"],
                "from": before["primaryCompletionDate"], "to": trial["primaryCompletionDate"]})
    # 사라진 시험은 대부분 검색어 경계의 흔들림이다. 개수만 남기고 알림을 만들지 않는다.
    changes["gone"] = sum(1 for nct in old_trials if nct not in new_trials)
    return changes


def _trim(changes: dict) -> dict:
    """변동 목록을 상한까지만 남기고, 잘라낸 개수를 함께 적는다."""
    out = dict(changes)
    for key in ("new", "statusChanged", "resultsPosted", "completionMoved"):
        rows = changes.get(key) or []
        out[key] = rows[:CHANGE_LIST_MAX]
        out[f"{key}Total"] = len(rows)
    return out


def refresh(path: Path = TRIALS_PATH) -> dict:
    """수집 → 이전분과 비교 → 저장. 반환값은 저장된 payload 전체."""
    previous = load(path)
    current = collect()
    first_run = previous is None
    changes = diff((previous or {}).get("trials") or {}, current["trials"])
    if first_run:
        # 첫 수집에는 비교 대상이 없다. 전 건이 "신규"로 잡히는데 그것은 변동이
        # 아니라 그냥 목록이다. 개수만 남긴다(0.9MB가 여기서 나왔다).
        changes = {**changes, "new": []}
    entry = {"at": current["fetchedAt"], "changes": _trim(changes), "firstRun": first_run}
    history = ((previous or {}).get("history") or [])[:CHANGES_KEEP - 1]
    payload = {**current, "history": [entry] + history}
    path.parent.mkdir(parents=True, exist_ok=True)
    # indent를 주지 않는다. 5,800건이면 들여쓰기만으로 파일이 두 배가 되고,
    # 이 파일은 매일 갱신되어 저장소에 커밋된다.
    path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", "utf-8")
    return payload


def load(path: Path = TRIALS_PATH) -> dict | None:
    try:
        payload = json.loads(path.read_text("utf-8"))
        return payload if isinstance(payload, dict) and payload.get("trials") else None
    except Exception:
        return None


def summarize(payload: dict) -> dict:
    """사이드바용 요약 수치. 활성 시험만 센다 — 철회·중단은 경쟁 시험이 아니다."""
    trials = payload.get("trials") or {}
    active = [t for t in trials.values() if t.get("status") in ACTIVE_STATUSES]
    by_family = {f: sum(1 for t in active if f in t.get("families", [])) for f in FAMILY_QUERIES}
    completed = sum(1 for t in trials.values() if t.get("status") == "COMPLETED")
    with_results = sum(1 for t in trials.values() if t.get("hasResults"))
    knee_explicit = sum(1 for t in trials.values() if t.get("kneeExplicit"))
    return {"total": len(trials), "active": len(active), "byFamily": by_family,
            "completed": completed, "withResults": with_results, "kneeExplicit": knee_explicit}
