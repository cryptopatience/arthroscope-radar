"""일일 스냅샷 생성 (scripts/daily.ts 포팅).

앱과 같은 run_analysis를 10개 저널 전체에 돌리고, Gemini 동향 분석·아이디어 제안을
덧붙여 data/daily.json에 저장한다. 실행: python scripts/daily.py
"""
from __future__ import annotations

import collections
import gzip
import hashlib
import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Windows 콘솔의 기본 인코딩(cp949)으로는 로그의 한글·엔대시를 못 찍는다.
# 출력을 파일이나 파이프로 넘길 때 UnicodeEncodeError로 작업 전체가 죽는다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

from radar import config  # noqa: E402
from radar.analysis import JOURNAL_ORDER, JOURNALS, run_analysis  # noqa: E402
from radar.cache import cache_key, reuse  # noqa: E402
from radar.gemini import (ENHANCE_MIN, enhance_idea, family_trend_report,  # noqa: E402
                          pmids_for_idea, resolve_model)
from radar.judge import (BLOCKED_VERDICTS, JUDGE_PROMPT_VERSION, JUDGE_RUNS, build_panel,  # noqa: E402
                         cache_versions, evidence_summary, judge_panel, judgment_cache_key,
                         titles_for_idea)
from radar.ncbi import NcbiCredentials  # noqa: E402
from radar import prior_art, selection  # noqa: E402

FAMILY_LABEL = {
    "arthroplasty": "관절성형 계열 (JOA·AT·BJJ·JBJS Am·CORR·Acta Orthop)",
    "arthroscopy": "관절경·스포츠의학 계열 (KSSTA·Arthroscopy·AJSM·OJSM)",
}
MONTHS_BACK = 12
IDEA_ABSTRACTS = 32
# 고도화는 최종 선정된 아이디어에만 한다. 후보 15개를 전부 고도화하면 비용이
# 세 배가 되는데, 그중 열 개는 어차피 전역 제약에서 떨어진다.
TREND_ABSTRACTS = 30
GEMINI_WEEKDAY = 4      # 0=월 … 4=금. 초록 수집은 매일, Gemini 분석은 이 요일에만.
# 어느 목록을 판정·고도화할지. 판정이 비용의 70%라 이 두 스위치가 비용을 정한다.
#
# 2026-08-25에 뒤집었다. 이전에는 "무릎 전체" 목록만 판정했는데(계열 판정을 꺼서
# 256 -> 97 호출로 줄인 결과였다), 그러다 보니 **판정이 붙는 유일한 목록이 하필
# 두 계열을 섞은 쪽**이 됐다. 섞인 풀에서는 관절경에만 사는 주제와 관절성형에만
# 사는 주제가 교차 공백으로 짝지어진다 — 그렇게 만들어진 후보 두 건이 전문가
# 눈가림 평가에서 structural로 채점됐다.
#
# 계열 안에서 뽑으면 그 조합이 구조적으로 생기지 않는다. 판정 대상 수는 계열당
# 6개 x 2 = 12개로 전체 목록 10개와 비슷한데, 실측 최종 결과물은 5개에서 8개로
# 늘었다(관절성형 5 + 관절경 3).
#
# 무릎 전체 목록은 계속 만들되 판정하지 않는다. 앱이 규칙 기반 점수로 그대로
# 보여주고, 판정·제안을 보려면 계열을 고르라고 안내한다.
AI_FAMILY_SCOPES = True
AI_ALL_SCOPE = False
SNAPSHOT = Path("data/daily.json")
# 실행 기록. 스냅샷은 "마지막 결과"만 담아서, 어제 수집이 돌긴 했는지·며칠째 조용한지를
# 알 수가 없다. 그래서 실행마다 한 줄씩 남기고 앱 사이드바가 그대로 읽는다.
RUN_LOG = Path("data/run_log.json")
RUN_LOG_KEEP = 90
# 이 실행을 그대로 재현하는 데 필요한 것 전부. 전문가 평가는 이 manifest가 고정된
# 뒤에 시작해야 한다 — 사전이나 임계값이 바뀐 뒤의 결과와 섞이면 평가가 무의미해진다.
MANIFEST = Path("data/run_manifest.json")
# 이번 실행에 실제로 들어간 제목·초록 원본. corpusContentHash는 "바뀌었다"만 알려주고
# 과거 내용을 복원하지는 못한다. 백테스트나 논문화를 하려면 그때의 입력이 그대로 있어야
# 한다. 저장소에는 커밋하지 않고(용량) 로컬에 두었다가 Release 자산으로 올린다.
CORPUS_DIR = Path("data/corpus")


def git_state() -> dict:
    """실행이 **시작될 때**의 commit. 기록 시점에 읽으면 실행 중 커밋한 것이 잡힌다.

    실제로 그렇게 됐다 — f449b88로 돌던 실행의 manifest에 962fed8이 찍혔다.
    작업 트리가 더러우면 그 사실도 남긴다. 커밋만으로는 재현되지 않기 때문이다.
    """
    def run(args):
        try:
            return subprocess.run(args, capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            return ""
    commit = run(["git", "rev-parse", "--short", "HEAD"]) or "unknown"
    dirty = bool(run(["git", "status", "--porcelain", "--untracked-files=no"]))
    return {"commit": commit, "dirty": dirty}


GIT_STATE = git_state()   # import 시점 = 실행 시작 시점


def _canonical(payload) -> str:
    """해시용 정규 직렬화. 키를 정렬하고 배열은 부르는 쪽에서 정렬해 넘긴다.

    순서만 바뀌어도 해시가 달라지면 재현성 확인에 쓸 수 없다.
    """
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(payload) -> str:
    return "sha256:" + hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def save_corpus(analysis: dict) -> dict:
    """이번 실행의 입력을 그대로 압축 보관한다. 커밋하지 않는다(.gitignore)."""
    records = sorted(
        ({"pmid": a["pmid"], "title": a["title"], "abstract": a["abstract"],
          "journal": a["journal"], "journalKey": a["journalKey"], "date": a["date"]}
         for a in analysis["articles"]),
        key=lambda r: r["pmid"])
    blob = _canonical(records).encode("utf-8")
    digest = _sha256(records)
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    # 파일명에 내용 해시를 넣어 불변으로 만든다. 날짜만 쓰면 같은 날 다시 돌릴 때
    # 앞선 실행의 입력을 덮어써 재현이 불가능해진다.
    stamp = analysis["dateTo"].replace("-", "")
    name = f"corpus-{stamp}-{digest.split(':')[1][:8]}.json.gz"
    path = CORPUS_DIR / name
    with gzip.open(path, "wb", compresslevel=9) as handle:
        handle.write(blob)
    packed = path.stat().st_size
    tag = f"corpus-{stamp}"
    log(f"코퍼스 원본 저장: {path} ({packed / 1048576:.2f}MB 압축 / {len(blob) / 1048576:.2f}MB 원본)")
    log(f"  Release 자산으로 올리려면:  gh release upload {tag} {path}")
    return {"assetName": name, "releaseTag": tag, "path": str(path).replace("\\", "/"),
            "sha256": digest, "sizeBytes": packed, "rawBytes": len(blob),
            "records": len(records), "uploaded": False,
            "note": "저장소에 커밋하지 않습니다. 백테스트·논문화를 위해 Release 자산으로 올리고 uploaded를 true로 바꾸세요."}


def write_manifest(analysis: dict, judgments: dict, prior: dict, selections: dict,
                   model: str, panel_models: list[str], run_ai: bool):
    """재현성 manifest. 무엇으로 언제 어떤 규칙으로 만든 결과인지 한 파일에 남긴다."""
    from radar import config
    from radar.judge import JUDGE_SCHEMA_VERSION
    from radar.vocabulary import CANONICAL_OUTCOME_VERSION, KEYWORD_DICT_VERSION
    thresholds = {name: getattr(config, name) for name in dir(config)
                  if name.isupper() and isinstance(getattr(config, name), (int, float, str))}
    payload = {
        "commit": GIT_STATE["commit"],
        "workingTreeDirty": GIT_STATE["dirty"],
        "cacheVersion": cache_versions(),
        "retrievalDate": analysis["dateTo"],
        "generatedAt": analysis["generatedAt"],
        "period": {"from": analysis["dateFrom"], "to": analysis["dateTo"]},
        "journalSet": [j["key"] for j in analysis["journals"]],
        "pubmedQueries": analysis.get("pubmedQueries") or {},
        "priorArtQueries": {k: v.get("query") for k, v in prior.items() if isinstance(v, dict)},
        "corpusPmids": sorted(a["pmid"] for a in analysis["articles"]),
        "corpusPmidsHash": _sha256(sorted(a["pmid"] for a in analysis["articles"])),
        # 초록·제목·저널·날짜까지 넣은 해시. PubMed가 사후 수정해도 차이가 드러난다.
        "corpusContentHash": _sha256(sorted(
            ([a["pmid"], a["title"], a["abstract"], a["journal"], a["date"]]
             for a in analysis["articles"]), key=lambda row: row[0])),
        "llmJudgmentHash": _sha256(judgments),
        "judgmentScopes": {scope: len(rows) for scope, rows in judgments.items()},
        "priorArtResultHash": _sha256(prior),
        "rawCorpusSnapshot": save_corpus(analysis),
        "thresholds": thresholds,
        "dictionaryVersion": KEYWORD_DICT_VERSION,
        "canonicalOutcomeVersion": CANONICAL_OUTCOME_VERSION,
        "promptVersion": JUDGE_PROMPT_VERSION,
        "judgmentSchemaVersion": JUDGE_SCHEMA_VERSION,
        "modelVersion": {"primary": model, "panel": panel_models, "judgeRuns": JUDGE_RUNS},
        # 판정마다 어떤 입력으로 나왔는지. 다음 실행의 캐시 판단 근거이자 감사 흔적이다.
        "judgmentInputHashes": {f"{scope}::{k}": v.get("judgmentInputHash")
                                for scope, rows in judgments.items()
                                for k, v in rows.items() if isinstance(v, dict)},
        "geminiRan": run_ai,
        "rawJudgmentsSaved": bool(judgments),
        "counts": {"articles": analysis["analyzed"], "candidates": len(analysis["ideas"]),
                   "judgments": sum(len(rows) for rows in judgments.values()), "priorArt": len(prior),
                   # 판정 범위가 여럿이면 전부 더한다. "all"만 세면 계열별로 돌린 날 0이 된다.
                   "final": sum(len(v.get("final") or []) for v in selections.values())},
    }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", "utf-8")
    log(f"manifest 저장: {MANIFEST} (commit {payload['commit']} · {payload['cacheVersion']})")


def report_summary(analysis: dict, all_judgments: dict, prior: dict, selections: dict):
    """고정 실행 결과 한 장. 전문가 평가 전에 이 숫자를 먼저 확정한다.

    판정이 붙은 범위마다 한 단락씩 낸다. 무릎 전체만 판정하던 때는 단락이 하나였는데,
    판정을 계열별로 돌리기로 하면서 "all"이 비어 요약이 통째로 미판정으로 나왔다.
    """
    line = "─" * 62
    out = [line, "고정 실행 요약", line]

    out.append(f"수집 논문 {analysis['analyzed']:,}편 (검색 {analysis['totalAvailable']:,}건 중 "
               f"초록 보유 {analysis['withAbstract']:,}편)")
    out.append("저널별: " + " · ".join(f"{JOURNALS[j['key']]['short']} {j['count']}" for j in analysis["journals"]))

    clusters = collections.Counter(t for a in analysis["articles"] for t in a["topics"])
    out.append("")
    out.append("클러스터별 논문 수:")
    for label, count in clusters.most_common():
        out.append(f"    {label:20} {count:5}")

    ok = [p for p in prior.values() if isinstance(p, dict) and not p.get("error")]
    if ok:
        out.append("")
        out.append(f"선행연구 검증 {len(ok)}건 / 대상 {len(prior)}건 (실패 {len(prior) - len(ok)}건)")

    scoped = [(k, v) for k, v in all_judgments.items() if v]
    if not scoped:
        out.append("")
        out.append("판정된 범위가 없습니다 (Gemini를 돌리지 않았거나 전부 실패).")
    for scope_key, judgments in scoped:
        label = "무릎 전체" if scope_key == "all" else FAMILY_LABEL.get(scope_key, scope_key)
        ideas = (analysis["ideas"] if scope_key == "all"
                 else analysis["ideasByFamily"].get(scope_key) or [])
        picked = selections.get(scope_key) or {}
        scores = picked.get("scores") or {}

        out.append("")
        out.append(line)
        out.append(f"[{label}] 감지된 clusterId × gapId: {len(ideas)}개")
        for idea in ideas:
            verdict = (judgments.get(idea["id"]) or {}).get("verdict", "미판정")
            score = scores.get(idea["id"]) or {}
            pa = prior.get(idea["id"]) or {}
            direct = pa.get("matchCount")
            out.append(f"    [{verdict:11}] {idea['id']:48} "
                       f"{idea['gapCategory']:31} 점수 {score.get('total', '-'):>3} "
                       f"선행 direct {direct if direct is not None else '미측정'}")

        verdicts = collections.Counter((judgments.get(i["id"]) or {}).get("verdict", "미판정") for i in ideas)
        out.append(f"판정 분포: {dict(verdicts)}")

        scoped_prior = [prior[i["id"]] for i in ideas if isinstance(prior.get(i["id"]), dict)
                        and not prior[i["id"]].get("error")]
        if scoped_prior:
            out.append(f"선행연구(후보 {len(scoped_prior)}개): "
                       f"direct {sum(p.get('matchCount') or 0 for p in scoped_prior)} · "
                       f"adjacent {sum(p.get('adjacentCount') or 0 for p in scoped_prior)} · "
                       f"background {sum(p.get('backgroundCount') or 0 for p in scoped_prior)}")

        final_ids = picked.get("final") or []
        final = [i for i in ideas if i["id"] in final_ids]
        eligible = [i for i in ideas if not (scores.get(i["id"]) or {}).get("ineligible")]
        prom = [i for i in final if i.get("outcomeSubtype") in ("prom", "prom_interpretation")]
        cats = collections.Counter(i["gapCategory"] for i in final)
        out.append(f"자격 통과 {len(eligible)}개 → 최종 선정 {len(final)}개 "
                   f"(조합 {picked.get('combinationsChecked', 0):,}가지 탐색)")
        out.append(f"PROM 아이디어 {len(prom)}개 (상한 {config.MAX_PROM_IDEAS}) · "
                   f"카테고리 분산 {len(cats)}종: {dict(cats)}")
        out.append(f"중복 제거 {len(picked.get('duplicates') or [])}개 · "
                   f"차단 {len(picked.get('blocked') or [])}개 · "
                   f"검증 대기 {len(picked.get('provisional') or [])}개")

        unmeasured = [i["id"] for i in ideas if "novelty" in ((scores.get(i["id"]) or {}).get("unscored") or [])]
        out.append(f"독창성 미측정 {len(unmeasured)}개")

        out.append("최종 아이디어:")
        for n, idea in enumerate(final, 1):
            score = scores.get(idea["id"]) or {}
            out.append(f"  {n}. [{idea['gapCategory']}] {idea['title']}")
            out.append(f"     {idea['id']} · 점수 {score.get('total')} · 축 {score.get('axes')}")
            out.append(f"     1차 결과: {idea['primaryEndpoint']}")

    errors = [k for k, v in prior.items() if isinstance(v, dict) and v.get("error")]
    out.append(line)
    out.append(f"선행연구 오류 {len(errors)}개")
    out.append(line)
    for row in out:
        log(row)


def env(name: str) -> str:
    return os.environ.get(name, "").strip()


def log(message: str):
    print(f"[daily] {message}", flush=True)


def load_previous() -> dict:
    """어제 스냅샷. Gemini 결과를 재사용하는 근거가 된다."""
    try:
        return json.loads(SNAPSHOT.read_text("utf-8"))
    except Exception:
        return {}


def append_run(entry: dict):
    """실행 한 건을 기록한다. 실패한 실행도 남겨야 "며칠째 안 돈다"가 보인다."""
    try:
        previous = json.loads(RUN_LOG.read_text("utf-8"))
        previous = previous if isinstance(previous, list) else []
    except Exception:
        previous = []
    RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
    RUN_LOG.write_text(json.dumps(([entry] + previous)[:RUN_LOG_KEEP], ensure_ascii=False, indent=1) + "\n", "utf-8")


def gemini_day(today: date, previous: dict) -> tuple[bool, str]:
    """오늘 Gemini를 돌릴지. 초록 수집 자체는 이 판단과 무관하게 매일 돈다."""
    if env("GEMINI_SKIP") == "1":
        return False, "GEMINI_SKIP=1 로 비활성화됨"
    if env("GEMINI_FORCE") == "1":
        return True, "GEMINI_FORCE=1 로 강제 실행"
    if not previous.get("suggestions") and not previous.get("trendReports"):
        return True, "이전 AI 결과가 없어 최초 1회 생성"
    if today.weekday() == GEMINI_WEEKDAY:
        return True, "금요일 정기 갱신"
    return False, f"{'월화수목금토일'[today.weekday()]}요일 — 지난 결과 재사용"


def judge_all(ideas, pool, scope, period, panel, prev: dict) -> dict:
    """공백이 실제 기회인지 분야 특성인지 먼저 거른다.

    3년치 백테스트에서 공백 수준의 연도간 상관이 0.9를 넘었다. 즉 대부분의 공백은
    채워질 빈칸이 아니라 그 분야의 고정된 특성이다. 통계로는 이 둘을 구분할 수 없어
    임상 지식이 필요하고, 그래서 고도화 앞에 판정 단계를 둔다.

    판정 한 건에 주 모델 JUDGE_RUNS회 + 나머지 판정자 1회씩을 쓴다. 값이 비싸 보이지만
    제목만 보내는 프롬프트이고 주 1회만 도는 데다, 이 반복이 화면의 "판정 안정성"과
    "모델 간 합의"를 만드는 유일한 재료다.
    """
    out = {}
    reused = 0
    for idea in ideas:
        # 버전 문자열이 같다는 것만으로 재사용하면 안 된다. Gemini에게 실제로 전달된
        # 입력(클러스터 통계·canonical·대표 논문 제목·범위)이 같아야 같은 판정이다.
        titles = titles_for_idea(idea, pool)
        ck = judgment_cache_key(idea, scope, period, titles, panel)
        cached = reuse(prev.get(idea["id"]), ck)
        if cached:
            out[idea["id"]] = cached
            reused += 1
            continue
        try:
            verdict = judge_panel(idea, titles, scope, period, panel)
            verdict["cacheKey"] = ck
            out[idea["id"]] = verdict
            evidence = evidence_summary(verdict, idea.get("metrics")) or {}
            log(f"  판정 {verdict['verdict']} · 안정성 {verdict['stability']['agree']}/{verdict['stability']['runs']}"
                f" · 합의 {verdict['consensus']} · 근거 {evidence.get('level', '-')} — {idea['title'][:28]}…")
        except Exception as error:
            out[idea["id"]] = {"error": str(error) or "판정 실패"}
            log(f"  판정 실패 — {scope}: {error}")
    if reused:
        log(f"  판정 캐시 재사용 {reused}건 (입력 해시 일치) — {scope}")
    return out


def check_prior_art(ideas, judgments, creds, prev: dict) -> dict:
    """후보마다 PubMed 전체를 다시 검색한다.

    최근 12개월 코퍼스로는 "지금 비어 있다"까지만 말할 수 있다. 3년 전에 같은
    연구가 이미 잘 수행됐는지는 더 긴 창으로 다시 봐야 안다. structural로 판정된
    후보는 어차피 최종에서 빠지므로 검색하지 않는다 — 호출을 아낀다.
    """
    out = {}
    for idea in ideas:
        verdict = ((judgments or {}).get(idea["id"]) or {}).get("verdict")
        if verdict in BLOCKED_VERDICTS:
            continue
        ck = cache_key("prior", idea["id"], str(config.PRIOR_ART_YEARS),
                       [cache_versions()], "ncbi")
        cached = reuse(prev.get(idea["id"]), ck)
        if cached:
            out[idea["id"]] = cached
            continue
        result = prior_art.check(idea, creds)
        result["cacheKey"] = ck
        out[idea["id"]] = result
        if result.get("error"):
            log(f"  선행연구 검색 실패 — {idea['id']}: {result['error']}")
        else:
            log(f"  선행연구 {result['matchCount']}편 (검색 {result['total']}건) — {idea['title'][:28]}…")
    return out


SCOPE_KEYS = ("all", *FAMILY_LABEL)


def _by_scope(stored: dict) -> dict:
    """범위별 구조로 정규화한다. 옛 스냅샷의 평평한 사전은 전역 범위로 본다."""
    if not stored:
        return {}
    if all(k in SCOPE_KEYS for k in stored):
        return stored
    return {"all": stored}


def _selection_summary(picked: dict) -> dict:
    """스냅샷에는 아이디어 본문이 아니라 id만 담는다. 본문은 analysis.ideas에 이미 있다."""
    return {"final": [i["id"] for i in picked["final"]],
            "blocked": [i["id"] for i in picked["blocked"]],
            "duplicates": [i["id"] for i in picked["duplicates"]],
            "scores": picked["allScores"],
            "distinctCategories": picked["distinctCategories"],
            "shortOfTarget": picked["shortOfTarget"],
            "combinationsChecked": picked.get("combinationsChecked", 0),
            "provisional": [i["id"] for i in picked.get("provisional") or []]}


def suggest_all(ideas, pool, trends, period, scope, creds, key, model, prev: dict) -> dict:
    out = {}
    for idea in ideas:  # 순차 호출: 야간 작업은 시간이 있고, 병렬 호출은 속도 제한에 걸리기 쉽다.
        try:
            pmids = pmids_for_idea(idea, pool, trends, IDEA_ABSTRACTS)
            if len(pmids) < ENHANCE_MIN:
                out[idea["id"]] = {"error": f"근거 초록이 {len(pmids)}편뿐이라 건너뛰었습니다."}
                continue
            ck = cache_key("idea", idea["id"], scope, pmids, model)
            cached = reuse(prev.get(idea["id"]), ck)
            if cached:
                out[idea["id"]] = cached
                log(f"  캐시 재사용 — {scope}: {idea['title'][:30]}…")
                continue
            result = enhance_idea(idea, pmids, trends, scope, period, creds, key, model)
            result["cacheKey"] = ck
            out[idea["id"]] = result
            log(f"  AI 제안 완료 — {scope}: {idea['title'][:30]}…")
        except Exception as error:
            out[idea["id"]] = {"error": str(error) or "AI 제안 실패"}
            log(f"  AI 제안 실패 — {scope}: {error}")
    return out


def main(started: datetime):
    creds = NcbiCredentials(env("NCBI_API_KEY"), env("NCBI_TOOL_EMAIL"))
    key, model = env("GEMINI_API_KEY"), resolve_model(env("GEMINI_MODEL"))
    panel = build_panel(key, model, env("GEMINI_JUDGE_MODEL_B"), env("OPENAI_API_KEY"), env("OPENAI_JUDGE_MODEL"))
    previous = load_previous()
    if not creds.api_key:
        log("경고: NCBI_API_KEY가 없습니다. 수집이 순차로 돌아 느려집니다.")
    if not key:
        log("경고: GEMINI_API_KEY가 없습니다. 규칙 기반 결과만 저장합니다.")

    to = date.today()
    month = to.month - MONTHS_BACK
    year = to.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    frm = to.replace(year=year, month=month, day=min(to.day, 28))
    date_from, date_to = frm.isoformat(), to.isoformat()
    log(f"기간 {date_from} – {date_to}")

    analysis = run_analysis(JOURNAL_ORDER, date_from, date_to, "", creds, progress=lambda f, m: log(f"{int(f*100):3d}% {m}"))
    log(f"분석 초록 {analysis['analyzed']}편, 아이디어 {len(analysis['ideas'])}개")

    period = f"{date_from}–{date_to}"
    prev_trends = previous.get("trendReports") or {}
    # 옛 스냅샷은 판정을 평평한 사전으로 담았다. 범위별 구조로 올려 읽는다.
    prev_judgments = _by_scope(previous.get("judgments") or {})
    prev_suggestions_raw = previous.get("suggestions") or {}
    prev_suggestions = _by_scope(prev_suggestions_raw)
    prev_prior = previous.get("priorArt") or {}
    now = datetime.now(timezone.utc).isoformat()

    run_ai, why = gemini_day(to, previous)
    if not key:
        run_ai, why = False, "GEMINI_API_KEY 없음"

    # 선행연구 검증은 NCBI만 쓰므로 Gemini 실행 여부와 무관하게 돈다. 유료 단계에
    # 묶어 두면 GEMINI_SKIP=1로 무료 확인을 할 때 독창성 축이 통째로 비어 버린다.
    # 지난 판정에서 structural이던 후보는 건너뛴다 — 어차피 최종에 못 온다.
    prior = {}
    log(f"선행연구 검증 (PubMed 전체, 최근 {config.PRIOR_ART_YEARS}년)")
    prior.update(check_prior_art(analysis["ideas"], prev_judgments.get("all") or {}, creds, prev_prior))
    for fam, fam_ideas in analysis["ideasByFamily"].items():
        prior.update(check_prior_art(fam_ideas, prev_judgments.get(fam) or {}, creds, prev_prior))
    for group in [analysis["ideas"], *analysis["ideasByFamily"].values()]:
        for idea in group:
            if idea["id"] in prior:
                idea["priorArt"] = prior[idea["id"]]

    if run_ai:
        log(f"Gemini 실행 — {why}")
        trend_reports, suggestions, judgments = {}, {}, {}
        selections = {}

        def family_pool(fam):
            members = next((f["journals"] for f in analysis["families"] if f["key"] == fam), [])
            return [a for a in analysis["articles"] if a["journalKey"] in members]

        for fam, label in FAMILY_LABEL.items():
            pool = family_pool(fam)
            ck = cache_key("trend", fam, label, [a["pmid"] for a in pool[:TREND_ABSTRACTS]], model)
            cached = reuse(prev_trends.get(fam), ck)
            if cached:
                trend_reports[fam] = cached
                log(f"동향 분석 캐시 재사용 — {fam}")
                continue
            try:
                report = family_trend_report(label, period, pool, analysis["trendsByFamily"].get(fam, []),
                                             creds, key, model, TREND_ABSTRACTS)
                report["cacheKey"] = ck
                trend_reports[fam] = report
                log(f"동향 분석 완료 — {fam}")
            except Exception as error:
                trend_reports[fam] = {"error": str(error) or "동향 분석 실패"}
                log(f"동향 분석 실패 — {fam}: {error}")

        # 범위별로 따로 담는다. 아이디어 id는 범위가 달라도 같으므로, 한 사전에 부으면
        # 계열 판정이 전역 판정을 덮어쓴다(실제로 40건 중 13건이 덮어써졌다).
        # 판정 프롬프트에 scope가 들어가므로 둘은 서로 다른 판정이다.
        if AI_ALL_SCOPE:
            log(f"공백 판정 — 무릎 전체 (판정단 {', '.join(j.name for j in panel)} · 주 모델 {JUDGE_RUNS}회 반복)")
            judgments["all"] = judge_all(analysis["ideas"], analysis["articles"], "무릎 전체", period,
                                         panel, (prev_judgments.get("all") or {}))
            counts = collections.Counter((judgments["all"].get(i["id"]) or {}).get("verdict", "실패")
                                         for i in analysis["ideas"])
            log(f"판정 결과 {dict(counts)}")

            picked = selection.select(analysis["ideas"], judgments["all"])
            selections["all"] = _selection_summary(picked)
            log(f"최종 선정 {len(picked['final'])}개 "
                f"(카테고리 {picked['distinctCategories']}종 · structural 차단 {len(picked['blocked'])} · "
                f"중복 제거 {len(picked['duplicates'])})")
            suggestions["all"] = suggest_all(picked["final"], analysis["articles"], analysis["trends"],
                                             period, "무릎 전체", creds, key, model,
                                             (prev_suggestions.get("all") or {}))
        else:
            log("무릎 전체 목록 판정 건너뜀 (AI_ALL_SCOPE=False) — 계열별 목록에서 판정합니다.")

        for fam, label in FAMILY_LABEL.items() if AI_FAMILY_SCOPES else []:
            fam_ideas = analysis["ideasByFamily"].get(fam) or []
            if not fam_ideas:
                continue
            pool = family_pool(fam)
            log(f"공백 판정 — {fam}")
            judgments[fam] = judge_all(fam_ideas, pool, label, period, panel,
                                       (prev_judgments.get(fam) or {}))
            fam_pick = selection.select(fam_ideas, judgments[fam])
            selections[fam] = _selection_summary(fam_pick)
            log(f"최종 선정 {len(fam_pick['final'])}개 — {fam}")
            suggestions[fam] = suggest_all(fam_pick["final"], pool,
                                           analysis["trendsByFamily"].get(fam, []),
                                           period, label, creds, key, model,
                                           (prev_suggestions.get(fam) or {}))
        if not AI_FAMILY_SCOPES:
            log("계열별 판정·고도화 건너뜀 (AI_FAMILY_SCOPES=False).")
        ai_refreshed_at = now
    else:
        # 초록은 오늘 것으로 갱신하되, AI 결과는 지난 것을 그대로 들고 간다.
        log(f"Gemini 건너뜀 — {why}. 지난 AI 결과 {len(prev_suggestions)}건을 유지합니다.")
        trend_reports = prev_trends
        suggestions, judgments = prev_suggestions, prev_judgments
        selections = previous.get("selections") or {}
        ai_refreshed_at = previous.get("aiRefreshedAt") or previous.get("generatedAt")

    snapshot = {"generatedAt": now, "model": model if key else None,
                "aiRefreshedAt": ai_refreshed_at, "aiRanToday": run_ai, "aiSkipReason": None if run_ai else why,
                "familyLabels": FAMILY_LABEL, "trendReports": trend_reports, "suggestions": suggestions,
                "judgments": judgments, "priorArt": prior, "selections": selections,
                "versions": cache_versions(), "analysis": analysis}
    out = SNAPSHOT
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(snapshot, ensure_ascii=False)
    out.write_text(text + "\n", "utf-8")
    log(f"저장 완료: {out} ({len(text.encode()) / 1048576:.2f}MB)")
    judged = sum(len(rows) for rows in judgments.values())
    suggested = sum(len(rows) for rows in suggestions.values())
    log(f"공백 판정 {judged}건({' · '.join(f'{k} {len(v)}' for k, v in judgments.items())}), "
        f"선행연구 {len(prior)}건, AI 제안 {suggested}건, "
        f"동향 분석 {len(trend_reports)}건 (오늘 Gemini 실행: {'예' if run_ai else '아니오'})")

    write_manifest(analysis, judgments, prior, selections, model,
                   [j.name for j in panel], run_ai)
    report_summary(analysis, judgments, prior, selections)

    failed = sum(1 for group in (*judgments.values(), *suggestions.values())
                 for v in group.values() if isinstance(v, dict) and v.get("error"))
    failed += sum(1 for v in trend_reports.values() if isinstance(v, dict) and v.get("error"))
    append_run({
        "at": now, "date": date_to, "ok": True, "seconds": round((datetime.now(timezone.utc) - started).total_seconds()),
        "collected": analysis["collected"], "analyzed": analysis["analyzed"],
        "withAbstract": analysis["withAbstract"], "totalAvailable": analysis["totalAvailable"],
        "capped": analysis["capped"], "journals": len(analysis["journals"]), "ideas": len(analysis["ideas"]),
        "sizeMB": round(len(text.encode()) / 1048576, 2),
        "ai": {"ran": run_ai, "reason": why, "model": model if key else None,
               "judged": judged, "suggested": suggested, "trends": len(trend_reports),
               "priorArt": len(prior),
               "final": sum(len(v.get("final") or []) for v in selections.values()),
               "failed": failed},
        "error": None,
    })


if __name__ == "__main__":
    start = datetime.now(timezone.utc)
    try:
        main(start)
    except Exception as error:
        # 실패도 기록한다. 로그가 비어 있는 날과 "돌았지만 실패한 날"은 다른 문제다.
        append_run({"at": datetime.now(timezone.utc).isoformat(), "date": date.today().isoformat(),
                    "ok": False, "seconds": round((datetime.now(timezone.utc) - start).total_seconds()),
                    "error": str(error) or error.__class__.__name__})
        print(f"[daily] 실패: {error}", file=sys.stderr)
        sys.exit(1)
