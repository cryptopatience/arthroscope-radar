"""스냅샷 하나를 읽어 파이프라인 건강도를 잰다. 실행: python scripts/validate.py

측정 기준(§15) 중 사람 라벨 없이 잴 수 있는 것만 계산한다. "전문가가 opportunity로
인정한 비율"이나 "실제 수행 가능하다고 평가된 비율"은 전문가 평가가 있어야 하므로
여기서 만들어 낼 수 없다 — 그 항목은 측정 불가로 표시한다. 없는 숫자를 지어내면
검증 자체가 무의미해진다.
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

from radar import selection  # noqa: E402
from radar.vocabulary import GAP_CATEGORIES  # noqa: E402

SNAPSHOT = Path("data/daily.json")
# 사람 라벨이 필요한 항목. 자동으로는 못 잰다.
NEEDS_EXPERT = [
    "최종 아이디어 중 전문가가 opportunity로 인정한 비율",
    "structural 오탐률",
    "실제 수행 가능하다고 평가된 비율",
]


def pct(part: int, whole: int) -> str:
    return f"{part}/{whole} ({round(part / whole * 100)}%)" if whole else f"{part}/0 (—)"


def main():
    if not SNAPSHOT.exists():
        print(f"{SNAPSHOT}가 없습니다. 먼저 python scripts/daily.py를 돌리세요.", file=sys.stderr)
        return 1
    snap = json.loads(SNAPSHOT.read_text("utf-8"))
    ideas = snap.get("analysis", {}).get("ideas") or []
    judgments = snap.get("judgments") or {}
    prior = snap.get("priorArt") or {}
    plan = selection.select(ideas, judgments)
    final = plan["final"]

    print(f"스냅샷 {snap.get('generatedAt', '?')[:19]} · 버전 {snap.get('versions', '(없음)')}")
    print(f"후보 {len(ideas)}개 → 최종 {len(final)}개\n")

    print("[판정]")
    verdicts = collections.Counter((judgments.get(i["id"]) or {}).get("verdict", "미판정") for i in ideas)
    print(f"  분포: {dict(verdicts)}")
    stabilities = [(judgments.get(i["id"]) or {}).get("stability") for i in ideas]
    rates = [s["rate"] for s in stabilities if isinstance(s, dict) and s.get("runs")]
    if rates:
        full = sum(1 for r in rates if r >= 1.0)
        print(f"  반복 실행 간 판정 일치도: 평균 {sum(rates) / len(rates):.2f} · 만장일치 {pct(full, len(rates))}")
    else:
        print("  반복 실행 간 판정 일치도: 측정 안 됨 (안정성 기록 없음)")
    split = sum(1 for i in ideas if (judgments.get(i["id"]) or {}).get("consensus") == "split")
    print(f"  모델 간 불일치: {pct(split, len(ideas))}")

    print("\n[최종 목록의 다양성]")
    cats = collections.Counter(selection.gap_category(i) for i in final)
    print(f"  서로 다른 gap category: {len(cats)}종 {dict(cats)}")
    missing = [c for c in GAP_CATEGORIES if c not in cats]
    print(f"  최종에 없는 카테고리: {', '.join(missing) if missing else '없음'}")
    prom = sum(1 for i in final if i.get("outcomeSubtype") in selection.PROM_SUBTYPES)
    print(f"  PROM 아이디어 비율: {pct(prom, len(final))}  (상한 1개)")
    gaps = [selection.source_gap_key(i) for i in final]
    sem = [selection.semantic_key(i) for i in final]
    print(f"  동일 클러스터×공백 중복: {pct(len(gaps) - len(set(gaps)), len(gaps))}  (0이어야 정상)")
    print(f"  의미상 중복(클러스터명 제거 후 동일 문장): {pct(len(sem) - len(set(sem)), len(sem))}  (0이어야 정상)")
    print(f"  후보 단계에서 중복으로 제거된 수: {len(plan['duplicates'])}")
    print(f"  훑은 조합 수: {plan.get('combinationsChecked', 0):,}")
    if plan.get("provisional"):
        print(f"  검증 대기(독창성 미측정): {len(plan['provisional'])}개")

    print("\n[선행연구 검증]")
    checked = [p for p in prior.values() if isinstance(p, dict)]
    ok = [p for p in checked if not p.get("error")]
    print(f"  검증 수행: {pct(len(ok), len(ideas))} · 실패 {len(checked) - len(ok)}건")
    if ok:
        hit = sum(1 for p in ok if p.get("matchCount", 0) > 0)
        print(f"  기존 동일 연구가 발견된 비율: {pct(hit, len(ok))}")
        counts = sorted(p.get("matchCount", 0) for p in ok)
        print(f"  일치 편수 분포: 최소 {counts[0]} · 중앙 {counts[len(counts) // 2]} · 최대 {counts[-1]}")
        empty = [p for p in ok if p.get("total", 0) == 0]
        if empty:
            print(f"  ⚠ 검색 결과가 0건인 질의 {len(empty)}건 — 검색어가 좁을 수 있습니다")
    unscored = [i["id"] for i in final if "novelty" in (plan["scores"].get(i["id"], {}).get("unscored") or [])]
    if unscored:
        print(f"  ⚠ 독창성 미측정 최종 아이디어 {len(unscored)}개 — 선행연구 결과가 없습니다")

    print("\n[표본·근거]")
    suff = collections.Counter((i.get("metrics") or {}).get("sufficiency", "미측정") for i in ideas)
    print(f"  표본 충분성: {dict(suff)}")
    reviewed = sum(1 for i in ideas if (i.get("canonical") or {}).get("reviewed"))
    print(f"  기준 결과변수가 전문가 확인된 후보: {pct(reviewed, len(ideas))}")

    print("\n[자동으로 잴 수 없는 항목 — 전문가 평가 필요]")
    for item in NEEDS_EXPERT:
        print(f"  · {item}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
