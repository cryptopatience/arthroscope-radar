"""백테스트 채점 — 과거 창에서 탐지한 공백이 미래 창에서 실제로 채워졌는가.

시계를 과거로 돌려 아이디어를 만들고(run_analysis는 이미 날짜를 인자로 받는다),
그 이후의 코퍼스로 채점한다. 탐지기·판정기가 미래를 예측하는지 숫자로 확인하는
유일한 방법이다.

"채워졌다"는 두 기준으로 나란히 잰다. 기준 하나에 걸면 그 기준 자체가 틀렸을 때
알 방법이 없다.

- 기준 A (공백 해소): 같은 공백을 미래 코퍼스에서 같은 탐지 조건(_passes)으로
  다시 쟀을 때 더 이상 공백이 아니고, 그 이유가 기준선 하락이 아니라 풀 자체의
  비율 상승인 경우. 탐지기의 자기 기준으로 채점하므로 순환처럼 보이지만,
  "탐지기가 공백이라 부른 것이 탐지기 기준으로도 사라졌는가"는 정당한 질문이다.
- 기준 B (직접 논문): 미래 창에서 그 속성을 가진 논문이 임계 편수 이상 나온 경우.
  절대 편수라 큰 클러스터에 유리하다 — 그래서 기대 편수(과거 비율 × 미래 표본)를
  함께 기록해 사람이 보정해 읽을 수 있게 한다.

채점의 핵심은 탐지기의 predicate를 재구성하는 것이다. generate_ideas의 람다는
직렬화되지 않으므로, 아이디어의 gapId에서 같은 predicate를 다시 만든다. 탐지기를
새로 추가하면 여기 build_predicate에도 짝을 추가해야 한다 — 짝이 없으면 채점을
건너뛰고 그렇다고 기록한다(조용히 틀린 기준으로 채점하지 않는다).
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from . import config
from .analysis import (NON_CLINICAL_DESIGNS, PROSPECTIVE_DESIGNS, _gap_metrics, _passes, _ts,
                       classify_subgroups)
from .judge import VERDICT_LABEL

# 기준 B 임계값. "이 공백을 직접 다룬 논문이 미래 창에 이만큼 나오면 채워진 것으로 본다."
BACKTEST_MIN_PAPERS = 5
# 미래 풀이 이보다 작으면 채점하지 않는다. 과거 탐지와 같은 최소 표본을 쓴다.
BACKTEST_MIN_POOL = config.IDEA_MIN_POOL
# 기준 A에서 "비율이 올랐다"로 인정할 최소 상승분(비율 단위). 0.5%p 미만은 잡음이다.
BACKTEST_MIN_GAIN = 0.005


def _ns(article: dict) -> SimpleNamespace:
    """분석 결과의 dict 초록을 _gap_metrics가 기대하는 속성 접근 객체로 바꾼다."""
    return SimpleNamespace(**article)


def _facet(a, name: str) -> bool:
    return name in (getattr(a, "facets", None) or [])


def _in_cluster(a, cluster: str) -> bool:
    return cluster in (getattr(a, "topics", None) or [])


def build_predicate(idea: dict):
    """아이디어의 gapId에서 탐지기와 같은 predicate·풀 정의를 재구성한다.

    반환: (spec, note) 또는 (None, 건너뛰는 이유).
    spec.pool/rest 는 미래 코퍼스를 받아 비교 풀을 만들고, spec.pred 가 속성 검사다.
    generate_ideas의 D1~D9와 짝이 맞아야 한다. 어긋나면 채점이 조용히 틀리므로,
    모르는 gapId는 반드시 None으로 돌려보낸다.
    """
    cluster = idea.get("clusterId") or ""
    raw_gap = idea.get("gapId") or ""
    gap = raw_gap.split("@")[0]
    note = ""
    if "@" in raw_gap:
        # mixed 하위유형(@variant)은 resolve()가 문서를 다시 봐야 나뉜다. 여기서는
        # 클러스터 전체로 근사하고 그렇다고 표시한다 — 하위유형 단위보다 관대한
        # 채점이 되므로, 채움률이 실제보다 조금 높게 나올 수 있는 방향이다.
        note = "mixed 하위유형을 클러스터 전체로 근사"
    if not cluster:
        return None, "클러스터 없음"

    def pool(arts):
        return [a for a in arts if _in_cluster(a, cluster)]

    def rest(arts):
        return [a for a in arts if not _in_cluster(a, cluster)]

    if gap == "prom_measurement":                                   # D1
        return SimpleNamespace(pool=pool, rest=rest, pred=lambda a: _facet(a, "prom")), note
    if gap == "prom_interpretation":                                # D2
        # PROM을 이미 잰 논문들 안에서 해석 여부를 본다.
        return SimpleNamespace(
            pool=lambda arts: [a for a in pool(arts) if _facet(a, "prom")],
            rest=lambda arts: [a for a in rest(arts) if _facet(a, "prom")],
            pred=lambda a: _facet(a, "prom_interpretation")), note
    if gap == "prospective_design":                                 # D3
        return SimpleNamespace(pool=pool, rest=rest,
                               pred=lambda a: getattr(a, "design", "") in PROSPECTIVE_DESIGNS), note
    if gap == "direct_comparison":                                  # D4
        return SimpleNamespace(pool=pool, rest=rest, pred=lambda a: _facet(a, "direct_comparison")), note
    if gap == "longterm_followup":                                  # D5
        return SimpleNamespace(pool=pool, rest=rest, pred=lambda a: _facet(a, "longterm")), note
    if gap == "clinical_utility":                                   # D6
        # 정확도를 보고한 논문들 안에서 환자 결과 동반 여부를 본다.
        return SimpleNamespace(
            pool=lambda arts: [a for a in pool(arts) if _facet(a, "accuracy_metric")],
            rest=lambda arts: [a for a in rest(arts) if _facet(a, "accuracy_metric")],
            pred=lambda a: _facet(a, "patient_outcome")), note
    if gap == "external_validation":                                # D7
        return SimpleNamespace(pool=pool, rest=rest, pred=lambda a: _facet(a, "external_validation")), note
    if gap.startswith("subgroup_"):                                 # D8
        label = gap[len("subgroup_"):]
        extra = "하위집단 표지는 미리보기 초록(420자)으로 재계산해 소폭 과소집계될 수 있음"
        return SimpleNamespace(
            pool=pool, rest=rest,
            pred=lambda a: label in classify_subgroups(
                f"{getattr(a, 'title', '')} {getattr(a, 'abstract', '')}")), (note + " · " + extra).strip(" ·")
    if gap.startswith("cross_"):                                    # D9
        second = gap[len("cross_"):]
        return SimpleNamespace(pool=pool, rest=rest,
                               pred=lambda a, t=second: _in_cluster(a, t)), note
    return None, f"알 수 없는 공백 종류 ({gap}) — build_predicate에 짝을 추가하세요"


def score_gap(idea: dict, judgment: dict | None, future_articles: list,
              future_from: str, future_to: str, stamps: dict) -> dict:
    """공백 하나를 미래 코퍼스에 대고 채점한다."""
    outcome = {
        "ideaId": idea.get("id", ""),
        "cluster": idea.get("clusterId", ""),
        "gapId": idea.get("gapId", ""),
        "gapCategory": idea.get("gapCategory", ""),
        "title": idea.get("title", ""),
        "verdict": (judgment or {}).get("verdict") or "미판정",
        "pastRatio": (idea.get("metrics") or {}).get("ratio"),
        "pastBaseline": (idea.get("metrics") or {}).get("baseline"),
        "pastN": (idea.get("metrics") or {}).get("n"),
        "skipped": "", "note": "",
    }
    spec, note = build_predicate(idea)
    outcome["note"] = note
    if spec is None:
        outcome["skipped"] = note
        return outcome

    pool = spec.pool(future_articles)
    rest = spec.rest(future_articles)
    if len(pool) < BACKTEST_MIN_POOL or not rest:
        outcome["skipped"] = f"미래 풀 부족 ({len(pool)}편)"
        return outcome

    midpoint = _ts(future_from) + ((_ts(future_to) + 86399) - _ts(future_from)) / 2
    m = _gap_metrics(pool, rest, spec.pred, stamps, midpoint)

    past_ratio = outcome["pastRatio"] or 0.0
    gain = m["ratio"] - past_ratio
    still_gap = _passes(m)
    # 기준 A: 미래에도 탐지 조건을 만족하면 공백이 유지된 것. 조건을 벗어났더라도
    # 비율이 오르지 않았다면 "코퍼스 전체가 그 지표를 덜 다루게 됐을 뿐"이므로
    # 채워진 것으로 세지 않는다.
    filled_ratio = (not still_gap) and gain >= BACKTEST_MIN_GAIN
    # 기준 B: 절대 편수. 큰 클러스터에 유리하므로 기대 편수를 같이 남긴다.
    expected = round(past_ratio * len(pool), 1)
    filled_count = m["observed"] >= BACKTEST_MIN_PAPERS

    if filled_ratio:
        ratio_note = "공백 해소 (비율 상승 + 탐지 조건 이탈)"
    elif still_gap:
        ratio_note = "공백 유지 (탐지 조건 계속 만족)"
    else:
        ratio_note = "탐지 조건은 벗어났으나 기준선 변동 탓 (비율 상승 없음)"

    outcome.update({
        "futureN": m["n"], "futureObserved": m["observed"],
        "futureRatio": m["ratio"], "futureBaseline": m["baseline"],
        "futureZ": m["z"], "futureEffectSize": m["effectSize"],
        "futureTemporal": (m.get("temporal") or {}).get("direction", "unknown"),
        "ratioGain": round(gain, 4), "expectedAtPastRate": expected,
        "filledByRatio": filled_ratio, "filledByCount": filled_count,
        "filledEither": filled_ratio or filled_count,
        "ratioNote": ratio_note,
    })
    return outcome


def score_all(ideas: list[dict], judgments: dict, future_articles: list[dict],
              future_from: str, future_to: str) -> list[dict]:
    arts = [_ns(a) for a in future_articles if a.get("design") not in NON_CLINICAL_DESIGNS]
    # 반기 비교용 타임스탬프는 공백마다 다시 만들 필요가 없다. 한 번 만들어 넘긴다.
    stamps = {a.pmid: _ts(a.date) for a in arts}
    out = []
    for idea in ideas:
        judgment = judgments.get(idea.get("id", "")) if isinstance(judgments, dict) else None
        judgment = judgment if isinstance(judgment, dict) and judgment.get("verdict") else None
        out.append(score_gap(idea, judgment, arts, future_from, future_to, stamps))
    return out


def summarize(outcomes: list[dict]) -> dict:
    """판정별 채움률. 이 표 하나가 백테스트의 결론이다.

    opportunity의 채움률이 structural보다 뚜렷이 높으면 판정기가 미래를 가른다는
    증거다. 비슷하면 판정 프롬프트를 고쳐야 한다는 신호다.
    """
    scored = [o for o in outcomes if not o["skipped"]]
    rows = {}
    for verdict in ("opportunity", "uncertain", "structural", "미판정"):
        group = [o for o in scored if o["verdict"] == verdict]
        if not group:
            continue
        rows[verdict] = {
            "count": len(group),
            "filledByRatio": sum(1 for o in group if o["filledByRatio"]),
            "filledByCount": sum(1 for o in group if o["filledByCount"]),
            "filledEither": sum(1 for o in group if o["filledEither"]),
        }
    return {"byVerdict": rows, "scored": len(scored),
            "skipped": [{"ideaId": o["ideaId"], "reason": o["skipped"]}
                        for o in outcomes if o["skipped"]]}


def _pct(numerator: int, denominator: int) -> str:
    return f"{round(numerator / denominator * 100)}%" if denominator else "—"


def report_markdown(summary: dict, outcomes: list[dict], meta: dict) -> str:
    lines = ["# ArthroScope 백테스트 보고서", "",
             f"- 과거 창 (아이디어 생성): {meta['pastFrom']} – {meta['pastTo']} · 초록 {meta['pastArticles']:,}편",
             f"- 미래 창 (채점): {meta['futureFrom']} – {meta['futureTo']} · 초록 {meta['futureArticles']:,}편",
             f"- 후보 {meta['ideas']}개 · 판정 {meta['judged']}개 · 채점 {summary['scored']}개"
             + (f" · 건너뜀 {len(summary['skipped'])}개" if summary["skipped"] else ""),
             f"- 생성 시각: {datetime.now(timezone.utc).isoformat()}", "",
             "## 판정별 채움률", "",
             "탐지기·판정기가 미래를 예측하는지는 이 표가 말한다. opportunity의 채움률이",
             "structural보다 뚜렷이 높아야 판정이 작동하는 것이다.", "",
             f"| 판정 | 건수 | 기준 A (공백 해소) | 기준 B (직접 논문 {BACKTEST_MIN_PAPERS}편 이상) | 둘 중 하나 |",
             "|---|---|---|---|---|"]
    for verdict, row in summary["byVerdict"].items():
        label = VERDICT_LABEL.get(verdict, verdict).split(" —")[0]
        lines.append(f"| {label} | {row['count']} | {row['filledByRatio']} ({_pct(row['filledByRatio'], row['count'])}) "
                     f"| {row['filledByCount']} ({_pct(row['filledByCount'], row['count'])}) "
                     f"| {row['filledEither']} ({_pct(row['filledEither'], row['count'])}) |")
    if summary["scored"] < 10:
        lines += ["", f"> 주의 — 채점된 공백이 {summary['scored']}개뿐입니다. 이 표는 경향을 보는 용도이지",
                  "> 통계적 근거가 아닙니다. `--candidates`를 올리거나 창을 넓혀 다시 돌리세요."]
    lines += ["",
              "> 기준 B는 절대 편수라 큰 클러스터에 유리합니다. 각 항목의 `기대 편수`",
              "> (과거 비율 × 미래 표본)와 견줘 읽으세요.", "",
              "## 공백별 상세", ""]
    for o in outcomes:
        lines += [f"### {o['cluster']} × {o['gapId']}", "", f"- 제목: {o['title']}",
                  f"- 판정: {VERDICT_LABEL.get(o['verdict'], o['verdict'])}"]
        if o["skipped"]:
            lines += [f"- 채점 건너뜀: {o['skipped']}", ""]
            continue
        lines += [
            f"- 과거: 비율 {round((o['pastRatio'] or 0) * 100, 1)}% (기준선 {round((o['pastBaseline'] or 0) * 100, 1)}%, {o['pastN']}편)",
            f"- 미래: 비율 {round(o['futureRatio'] * 100, 1)}% (기준선 {round(o['futureBaseline'] * 100, 1)}%, "
            f"{o['futureN']}편 중 {o['futureObserved']}편 · 기대 편수 {o['expectedAtPastRate']})",
            f"- 기준 A: {'충족' if o['filledByRatio'] else '미충족'} — {o['ratioNote']}",
            f"- 기준 B: {'충족' if o['filledByCount'] else '미충족'} — 직접 논문 {o['futureObserved']}편"
            f" (임계 {BACKTEST_MIN_PAPERS}편)"]
        if o["note"]:
            lines.append(f"- 참고: {o['note']}")
        lines.append("")
    if summary["skipped"]:
        lines += ["## 건너뛴 항목", ""]
        lines += [f"- {s['ideaId']}: {s['reason']}" for s in summary["skipped"]]
        lines.append("")
    return "\n".join(lines)
