"""ArthroScope Research Radar — Streamlit 앱 (app/page.tsx 포팅).

실행: streamlit run app.py
"""
from __future__ import annotations

import hmac
import html as html_lib
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

from radar import config
from radar.analysis import FAMILIES, FAMILY_ORDER, JOURNAL_ORDER, JOURNALS, AnalysisError, run_analysis
from radar.cache import cache_key, get as cache_get, load as cache_load, put as cache_put
from radar.gemini import ENHANCE_MIN, enhance_idea, pmids_for_idea, resolve_model
from radar.judge import VERDICT_LABEL, evidence_summary
from radar.backtest import BACKTEST_MIN_PAPERS, latest_report as latest_backtest
from radar.selection import PROM_SUBTYPES, gap_category, select
from radar.vocabulary import GAP_CATEGORIES, LONGTERM_SUBTYPES
from radar.ncbi import NcbiCredentials
from radar.trials import (ACTIVE_STATUSES, FAMILY_LABEL as TRIAL_FAMILY_LABEL, STATUS_LABEL,
                          load as load_trials, summarize as summarize_trials, trial_url)

TREND_ROWS = 9          # 편수 순 표에서 먼저 보여주는 행 수. 그 아래 상승 신호는 따로 덧붙인다.
ARTICLE_PAGE = 8
SNAPSHOT_PATH = Path("data/daily.json")
RUN_LOG_PATH = Path("data/run_log.json")     # 일일 작업이 실행마다 한 줄씩 남기는 기록
SAVED_PATH = Path("data/saved_ideas.json")   # localStorage 대신 로컬 파일에 저장
RUN_LOG_ROWS = 14                            # 사이드바 표에 보여줄 최근 실행 수
STALE_HOURS = 36                             # 이 시간을 넘겨 조용하면 경고한다 (하루 1회 실행 + 여유)

st.set_page_config(page_title="ArthroScope Research Radar", page_icon="🦵", layout="wide")

st.markdown("""
<style>
:root { --ink:#10231f; --muted:#61716c; --coral:#ff6b54; --mint:#9be3c8; --card:#fbf9f4; --line:#d9d2c5; }
.eyebrow { color:var(--coral); font-size:11px; font-weight:800; letter-spacing:.19em; margin-bottom:6px; }
.hero-title { font-family: Georgia, serif; font-size:44px; line-height:1.0; letter-spacing:-.04em; margin:0 0 10px; }
.hero-title em { color:var(--coral); font-style:normal; }
.hero-text { color:var(--muted); font-size:15px; max-width:720px; }
.sec { display:flex; align-items:baseline; gap:12px; border-bottom:1px solid var(--ink); margin:36px 0 14px; padding-bottom:8px; }
.sec i { color:var(--coral); font-family:Georgia, serif; font-size:15px; }
.sec h2 { font-family:Georgia, serif; font-size:26px; margin:0; letter-spacing:-.02em; }
.sec p { margin:0 0 0 auto; color:var(--muted); font-size:11px; }
.pill { display:inline-block; padding:2px 9px; border-radius:12px; font-size:11px; font-weight:700; margin-right:4px; }
.pill.rising { background:#d7f7e7; color:#176b48; } .pill.cooling { background:#ffe2dc; color:#a4261d; }
.pill.steady { background:#eee9df; color:#4f5a56; } .pill.sparse { background:#f0f0f0; color:#888; }
.tag { display:inline-block; background:#eae4d8; color:var(--ink); font-size:11px; padding:2px 8px; border-radius:4px; margin:0 4px 4px 0; }
.insight { border:1px solid var(--ink); background:var(--card); padding:18px 20px; box-shadow:6px 6px 0 var(--ink); }
.insight .lbl { color:var(--coral); font-size:10px; font-weight:800; letter-spacing:.18em; }
.insight h3 { font-family:Georgia, serif; font-size:20px; margin:8px 0; }
.insight p { font-size:13px; color:var(--muted); line-height:1.6; }
.formula { margin-top:10px; padding:8px 10px; background:#10231f; color:#b9f4dc; font-size:12px; }
.ai { border-left:4px solid var(--mint); background:#f2f9f5; padding:14px 18px; margin:8px 0 18px; }
.ai .lbl { color:#176b48; font-size:10px; font-weight:800; letter-spacing:.15em; }
.ai h3 { font-family:Georgia, serif; margin:6px 0; font-size:20px; }
.verdict { border-left:3px solid #b9b0a0; background:#f6f3ec; padding:10px 14px; margin:6px 0 10px; font-size:12.5px; color:#4a544f; }
.verdict b { color:var(--ink); }
.verdict.op { border-left-color:#2f9e6e; background:#f1f9f5; }
.verdict .std { display:block; margin-top:6px; color:#61716c; font-size:11.5px; }
.verdict .hd { display:block; font-size:10px; font-weight:800; letter-spacing:.14em; color:#8a7f6d; margin-bottom:5px; }
.verdict.op .hd { color:#2f9e6e; }
.verdict dl { display:grid; grid-template-columns:max-content 1fr; gap:3px 14px; margin:9px 0 0; padding-top:8px; border-top:1px solid rgba(0,0,0,.08); font-size:11.5px; }
.verdict dt { color:var(--muted); white-space:nowrap; }
.verdict dd { margin:0; color:var(--ink); }
.lv { display:inline-block; padding:1px 8px; border-radius:10px; font-weight:700; font-size:11px; }
.lv.high { background:#d7f7e7; color:#176b48; } .lv.mid { background:#eee9df; color:#4f5a56; }
.lv.low { background:#ffe2dc; color:#a4261d; }
.muted-card { opacity:.92; }
.gapcat { display:inline-block; background:#10231f; color:#b9f4dc; font-size:10px; font-weight:700;
          letter-spacing:.08em; padding:2px 9px; border-radius:3px; margin-right:6px; }
.scores { display:flex; flex-wrap:wrap; gap:10px; margin:8px 0 2px; font-size:11.5px; color:var(--muted); }
.scores b { color:var(--ink); }
.scores .na { color:#a4261d; }
.prior { border-left:3px solid #c8bda8; background:#faf7f1; padding:9px 13px; margin:6px 0;
         font-size:12px; color:#4a544f; }
.prior b { color:var(--ink); }
.caveat { border-left:4px solid var(--coral); background:#fff3f0; padding:12px 16px; font-size:13px; margin:16px 0; }
.article { border-top:1px solid var(--line); padding:12px 0; }
.article .meta { color:var(--muted); font-size:11px; margin-bottom:4px; }
.article h4 { margin:0 0 6px; font-size:15px; } .article p { font-size:13px; color:#333; margin:0 0 4px; }
.article .authors { font-size:11px; color:var(--muted); }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# 설정·상태
# ---------------------------------------------------------------------------

def esc(value) -> str:
    """HTML로 내보낼 외부 문자열. PubMed 초록과 Gemini 응답에는 부등호가 들어 있다 —
    2,038편 중 30편이 그렇다("aged <50 years", "BMI >30"). 그대로 넣으면 브라우저가
    태그로 읽어 뒤 문장을 통째로 삼킨다."""
    return html_lib.escape(str(value if value is not None else ""))


def secret(key: str) -> str:
    try:
        value = st.secrets.get(key)  # type: ignore[attr-defined]
        if value:
            return str(value).strip()
    except Exception:
        pass
    return os.environ.get(key, "").strip()


def credentials() -> NcbiCredentials:
    return NcbiCredentials(api_key=secret("NCBI_API_KEY"), email=secret("NCBI_TOOL_EMAIL"))


def require_login():
    """APP_PASSWORD가 설정돼 있을 때만 잠근다. 비워 두면(로컬 개발) 그냥 열린다.

    세션 단위 인증이라 브라우저를 새로고침하면 다시 물어본다.
    """
    password = secret("APP_PASSWORD")
    if not password or st.session_state.get("authed"):
        return

    st.markdown('<div class="eyebrow">ORTHOPAEDIC INTELLIGENCE</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="hero-title">ArthroScope<br><em>Research Radar</em></h1>', unsafe_allow_html=True)
    st.markdown('<p class="hero-text">비공개 연구 도구입니다. 접근 암호를 입력해 주세요.</p>', unsafe_allow_html=True)
    with st.form("login"):
        entered = st.text_input("접근 암호", type="password", label_visibility="collapsed",
                                placeholder="접근 암호")
        submitted = st.form_submit_button("입장 ↗", type="primary")
    if submitted:
        # 바이트로 비교한다. compare_digest는 비ASCII 문자열을 직접 받으면 TypeError를 낸다(한글 암호).
        if hmac.compare_digest(entered.encode("utf-8"), password.encode("utf-8")):
            st.session_state.authed = True
            st.rerun()
        st.error("암호가 일치하지 않습니다.")
    st.stop()


def init_state():
    defaults = {
        "analysis": None, "snapshot": None, "error": "",
        "scope": ("all", None), "active_topic": "전체", "visible_articles": ARTICLE_PAGE,
        "enhanced": {}, "enhance_error": {}, "show_saved": False, "booted": False,
        # 임상시험 레이더는 아이디어 파이프라인과 독립이다. 상태도 따로 둔다.
        "show_trials": False, "trials_family": "전체", "trials_status": "활성만",
        "trials_knee_only": True,
        # 백테스트는 앱에서 돌리지 않는다 — 결과 파일만 읽어 보여준다.
        "show_backtest": False,
        "saved_ideas": load_saved(),
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def load_saved() -> list[dict]:
    try:
        return json.loads(SAVED_PATH.read_text("utf-8"))
    except Exception:
        return []


def persist_saved():
    try:
        SAVED_PATH.parent.mkdir(parents=True, exist_ok=True)
        SAVED_PATH.write_text(json.dumps(st.session_state.saved_ideas, ensure_ascii=False, indent=2), "utf-8")
    except Exception:
        pass  # 저장은 편의 기능. 파일 시스템이 거부하면 메모리에만 둔다.


def github_json(path: str):
    """비공개 저장소에서 파일 하나를 읽는다. 설정이 없으면 조용히 포기한다."""
    repo, token = secret("GITHUB_REPO"), secret("GITHUB_TOKEN")
    branch = secret("GITHUB_BRANCH") or "main"
    if not repo or not token:
        return None
    try:
        response = requests.get(
            f"https://api.github.com/repos/{repo}/contents/{path}",
            params={"ref": branch},
            headers={"authorization": f"Bearer {token}", "accept": "application/vnd.github.raw+json",
                     "user-agent": "arthroscope-research-radar", "x-github-api-version": "2022-11-28"},
            timeout=30,
        )
        return response.json() if response.ok else None
    except Exception:
        return None


def load_snapshot() -> dict | None:
    """일일 스냅샷: 로컬 파일 → GitHub 비공개 저장소 순으로 시도."""
    if SNAPSHOT_PATH.exists():
        try:
            payload = json.loads(SNAPSHOT_PATH.read_text("utf-8"))
            if payload.get("analysis", {}).get("articles"):
                return payload
        except Exception:
            pass
    payload = github_json("data/daily.json")
    return payload if isinstance(payload, dict) and payload.get("analysis", {}).get("articles") else None


@st.cache_data(ttl=300, show_spinner=False)
def load_run_log() -> list[dict]:
    """실행 기록. 스냅샷과 같은 자리(로컬 → GitHub)에서 읽는다.

    스냅샷만으로는 "마지막에 언제 돌았나"까지만 알 수 있다. 매일 돌고 있는지, 어느 날
    건너뛰었는지, 돌았는데 실패했는지는 이 기록이 있어야 보인다.
    """
    if RUN_LOG_PATH.exists():
        try:
            rows = json.loads(RUN_LOG_PATH.read_text("utf-8"))
            if isinstance(rows, list):
                return rows
        except Exception:
            pass
    rows = github_json("data/run_log.json")
    return rows if isinstance(rows, list) else []


def run(journals: list[str], date_from: str, date_to: str, focus: str):
    bar = st.progress(0.0, text="PubMed 검색 준비 중")
    try:
        result = run_analysis(journals, date_from, date_to, focus, credentials(),
                              progress=lambda f, m: bar.progress(f, text=m))
        st.session_state.analysis = result
        st.session_state.snapshot = None
        st.session_state.error = ""
        st.session_state.active_topic = "전체"
        st.session_state.visible_articles = ARTICLE_PAGE
        # 현재 범위를 새 결과가 지원하면 유지한다. 한 계열을 보던 사람을 매번 전체로 되돌리지 않는다.
        kind, key = st.session_state.scope
        if kind == "family" and key not in result["trendsByFamily"]:
            st.session_state.scope = ("all", None)
        if kind == "journal" and key not in result["trendsByJournal"]:
            st.session_state.scope = ("all", None)
    except AnalysisError as error:
        st.session_state.error = str(error)
    except Exception as error:
        st.session_state.error = str(error) or "분석 중 오류가 발생했습니다."
    finally:
        bar.empty()


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def signal_label(signal: str) -> str:
    return {"rising": "상승", "cooling": "감소", "sparse": "표본 부족"}.get(signal, "유지")


def compact_date(value: str) -> str:
    if not value:
        return "날짜 미상"
    parts = value.split("-")
    return f"{parts[0]}.{parts[1] if len(parts) > 1 else '01'}"


def fmt_dt(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%Y.%m.%d %H:%M")
    except Exception:
        return iso


def pubmed(pmid: str) -> str:
    return f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"


def scope_info(analysis: dict):
    kind, key = st.session_state.scope
    if kind == "family":
        fam = next((f for f in analysis["families"] if f["key"] == key), None)
        return analysis["trendsByFamily"].get(key, []), (fam or {}).get("label", ""), (fam or {}).get("count", 0)
    if kind == "journal":
        j = next((j for j in analysis["journals"] if j["key"] == key), None)
        return analysis["trendsByJournal"].get(key, []), JOURNALS[key]["short"], (j or {}).get("count", 0)
    return analysis["trends"], "선택한 저널 전체", analysis["analyzed"]


def in_scope(article: dict) -> bool:
    kind, key = st.session_state.scope
    if kind == "family":
        return JOURNALS[article["journalKey"]]["family"] == key
    if kind == "journal":
        return article["journalKey"] == key
    return True


def active_ideas(analysis: dict) -> tuple[list[dict], bool]:
    """범위에 아이디어가 없으면 전체 기준으로 대체하고 그렇다고 표시한다."""
    kind, key = st.session_state.scope
    if kind == "all":
        return analysis["ideas"], False
    scoped = (analysis["ideasByFamily"] if kind == "family" else analysis["ideasByJournal"]).get(key)
    return (scoped, False) if scoped else (analysis["ideas"], True)


def suggestion_for(idea_id: str) -> dict | None:
    if idea_id in st.session_state.enhanced:
        return st.session_state.enhanced[idea_id]
    return _scoped("suggestions").get(idea_id)


def default_scope(snap: dict) -> tuple:
    """첫 화면을 판정이 붙어 있는 범위로 연다.

    어느 범위를 판정할지는 야간 작업 설정이 정한다(무릎 전체 -> 계열별로 옮겼다).
    기본을 하나로 고정해 두면 그 설정을 바꾼 날 첫 화면이 통째로 미판정 목록이 된다.
    """
    stored = snap.get("judgments") or {}
    if stored.get("all"):
        return ("all", None)
    by_family = (snap.get("analysis") or {}).get("ideasByFamily") or {}
    for fam in FAMILY_ORDER:
        if stored.get(fam) and by_family.get(fam):
            return ("family", fam)
    return ("all", None)


def scope_key() -> str:
    """스냅샷에서 판정·제안을 찾을 범위 열쇠. 저널 단위는 따로 판정하지 않으므로 전역을 쓴다."""
    kind, key = st.session_state.scope
    return key if kind == "family" else "all"


def _scoped(section: str) -> dict:
    """범위별로 저장된 판정·제안. 옛 스냅샷은 평평한 사전이라 그대로 전역으로 읽는다.

    아이디어 id는 범위가 달라도 같다. 한 사전에 부으면 계열 판정이 전역 판정을
    덮어쓴다 — 실제로 40건 중 13건이 그렇게 사라졌다.
    """
    stored = (st.session_state.snapshot or {}).get(section) or {}
    if stored and set(stored) <= {"all", *FAMILY_ORDER}:
        return stored.get(scope_key()) or {}
    return stored      # 옛 형식(평평한 사전)


def judgment_for(idea_id: str) -> dict | None:
    """공백이 실제 기회인지 분야 특성인지에 대한 AI 판정. 스냅샷에만 있다."""
    judgment = _scoped("judgments").get(idea_id)
    return judgment if isinstance(judgment, dict) and judgment.get("verdict") in VERDICT_LABEL else None


# "미측정"은 아직 재지 않았다는 뜻이라 중립색으로 둔다(낮음과 같은 자리에 두면 안 된다).
LEVEL_CLASS = {"높음": "high", "중간": "mid", "낮음": "low", "미측정": "mid"}


def verdict_html(idea: dict) -> str:
    """판정과 그 판정의 근거 수준. 모델이 스스로 매긴 확신도 대신 밖에서 잰 네 가지를 쓴다."""
    judgment = judgment_for(idea["id"])
    if not judgment:
        return ""
    verdict = judgment["verdict"]
    opportunity = verdict == "opportunity"
    head = "판정 근거" if opportunity else "추천하지 않는 이유"
    block = (f'<div class="verdict {"op" if opportunity else ""}"><span class="hd">{head}</span>'
             f'<b>{VERDICT_LABEL[verdict]}</b><br>{esc(judgment.get("reason"))}')
    if judgment.get("fieldStandard"):
        block += f'<span class="std">이 분야가 실제로 쓰는 1차 결과: {esc(judgment["fieldStandard"])}</span>'
    evidence = evidence_summary(judgment, idea.get("metrics"))
    if evidence:
        rows = [("판정 안정성", evidence["stability"]), ("모델 간 합의", evidence["consensus"]),
                ("시간적 근거", evidence["temporal"]), ("표본 충분성", evidence["sample"])]
        block += "<dl>" + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows)
        block += (f'<dt>종합 근거 수준</dt><dd><span class="lv {LEVEL_CLASS.get(evidence["level"], "mid")}">'
                  f'{evidence["level"]}</span></dd></dl>')
    return block + "</div>"


def plan_ideas(ideas: list[dict]) -> dict:
    """후보를 최종·구조적·중복·미판정으로 가른다.

    앱에서 직접 계산한다. 스냅샷에도 결과가 들어 있지만, 실시간 분석에는 없고
    옛 스냅샷에도 없다. 순수 계산이라(후보 15개 → 조합 3,003가지) 매번 돌려도 된다.
    """
    # 범위별로 나뉜 사전을 그대로 넘기면 select()가 아이디어 id로 찾지 못해 전부
    # "판정 없음"이 된다. 바로 아래 judged 는 judgment_for(범위를 여는 함수)로 만드는데
    # select 에는 안 열고 넘기고 있었다 — 한 함수 안에서 두 방식이 섞여 있었다.
    judgments = _scoped("judgments")
    judged = {i["id"] for i in ideas if judgment_for(i["id"])}
    pending = [i for i in ideas if i["id"] not in judged]
    if not judged:
        return {"final": [], "structural": [], "duplicates": [], "pending": pending,
                "provisional": [], "scores": {}, "distinctCategories": 0, "shortOfTarget": False}
    picked = select([i for i in ideas if i["id"] in judged], judgments)
    final_ids = {i["id"] for i in picked["final"]}
    dup_ids = {i["id"] for i in picked["duplicates"]}
    blocked_ids = {i["id"] for i in picked["blocked"]}
    # 차단된 것 중 진짜 구조적 공백과 "카테고리가 없어서 빠진 것"을 나눈다.
    # 옛 스냅샷의 아이디어에는 공백 유형이 없는데, 그것을 구조적 공백이라고 부르면
    # 판정하지 않은 것을 판정한 것처럼 보여주게 된다.
    structural = [i for i in picked["blocked"]
                  if (judgment_for(i["id"]) or {}).get("verdict") == "structural"]
    unclassified = [i for i in picked["blocked"] if i not in structural]
    # 최종에 못 든 나머지(uncertain·제약 탈락)는 검토 대상으로 남긴다.
    prov_ids = {i["id"] for i in picked.get("provisional") or []}
    leftover = [i for i in ideas if i["id"] in judged
                and i["id"] not in final_ids | dup_ids | blocked_ids | prov_ids]
    return {"final": picked["final"], "structural": structural,
            "duplicates": picked["duplicates"] + leftover + unclassified, "pending": pending,
            "provisional": picked.get("provisional") or [],
            "scores": picked["allScores"], "distinctCategories": picked["distinctCategories"],
            "shortOfTarget": picked["shortOfTarget"],
            "combinationsChecked": picked.get("combinationsChecked", 0)}


AXIS_LABEL = {"evidence_strength": "근거", "novelty": "독창성", "clinical_importance": "임상 중요성",
              "methodological_advance": "방법론", "feasibility": "실현성"}


def scores_html(score: dict | None) -> str:
    if not score:
        return ""
    cells = []
    for axis, label in AXIS_LABEL.items():
        value = score["axes"].get(axis)
        cells.append(f'<span>{label} <b>{value}/5</b></span>' if value is not None
                     else f'<span class="na">{label} <b>미측정</b></span>')
    cells.append(f'<span>합계 <b>{score["total"]}</b></span>')
    return '<div class="scores">' + "".join(cells) + "</div>"


def prior_art_html(idea: dict) -> str:
    """가장 유사한 선행연구. 최근 12개월 코퍼스로는 못 하는 판단이라 따로 보여준다."""
    prior = idea.get("priorArt")
    if not isinstance(prior, dict):
        return ""
    if prior.get("error"):
        return f'<div class="prior"><b>선행연구 확인 실패</b> — {esc(prior["error"])}</div>'
    direct = prior.get("matchCount")
    if direct is None:
        head = "<b>선행연구 미측정</b>"
    else:
        head = (f'<b>선행연구 직접 {direct}편</b> · 인접 {prior.get("adjacentCount", 0)}편 '
                f'· 배경 {prior.get("backgroundCount", 0)}편')
    block = (f'<div class="prior">{head} '
             f'(PubMed 전체 최근 {prior.get("years", "?")}년 · 검색 {prior.get("total", 0)}건 중 '
             f'초록 {prior.get("examined", 0)}편 확인)')
    for label, key in (("직접", "matches"), ("인접", "adjacent")):
        for m in (prior.get(key) or [])[:2]:
            block += (f'<br>· [{label}] <a href="{pubmed(m["pmid"])}" target="_blank">PMID {m["pmid"]}</a> '
                      f'({esc(m.get("year"))}, {esc(m.get("journal"))}) {esc(m.get("title", "")[:85])}')
    if prior.get("note"):
        block += f'<br><i>{esc(prior["note"])}</i>'
    return block + "</div>"


def is_saved(idea_id: str) -> bool:
    return any(i["id"] == idea_id for i in st.session_state.saved_ideas)


def toggle_saved(idea: dict, scope_label: str):
    if is_saved(idea["id"]):
        st.session_state.saved_ideas = [i for i in st.session_state.saved_ideas if i["id"] != idea["id"]]
    else:
        st.session_state.saved_ideas = [{**idea, "savedAt": datetime.now().isoformat(), "scope": scope_label}] + st.session_state.saved_ideas
    persist_saved()


def saved_markdown() -> str:
    saved = st.session_state.saved_ideas
    lines = ["# 저장한 논문 아이디어", "", f"- 내보낸 날짜: {date.today().isoformat()}", f"- 저장 개수: {len(saved)}개", ""]
    for n, idea in enumerate(saved, 1):
        lines += [f"## {n}. {idea['title']}", "", f"- 저장 시각: {fmt_dt(idea.get('savedAt', ''))}",
                  f"- 저장 범위: {idea.get('scope', '')}", f"- 태그: {', '.join(idea['tags'])}",
                  f"- 독창성 {idea['novelty']}/5 · 실현성 {idea['feasibility']}/5", "", idea["rationale"], "",
                  f"- PICO: {idea['pico']}", f"- 권장 설계: {idea['design']}", f"- 1차 결과: {idea['primaryEndpoint']}"]
        lines += [f"- 근거: [PMID {e['pmid']}]({pubmed(e['pmid'])}) — {e['title']}" for e in idea["evidence"]]
        lines.append("")
    lines += ["---", "", "<!-- 복원용 데이터. 이 블록을 남겨두면 나중에 목록을 그대로 되살릴 수 있습니다. -->",
              "```json", json.dumps(saved, ensure_ascii=False, indent=2), "```"]
    return "\n".join(lines)


def report_markdown(analysis: dict, trends: list[dict], scope_label: str, scope_count: int, ideas: list[dict], fell_back: bool) -> str:
    lines = ["# ArthroScope Research Radar", "",
             f"- 분석 기간: {analysis['dateFrom']}–{analysis['dateTo']}",
             f"- 분석 초록: {analysis['analyzed']}편 / 검색 결과 {analysis['totalAvailable']}편",
             f"- 보고 범위: {scope_label} ({scope_count}편)" + (" (아이디어는 저널 단독 신호 부족으로 전체 기준)" if fell_back else ""),
             f"- 검색 초점: {analysis['query'] or '전체'}", "", "## 주요 트렌드"]
    for t in trends[:8]:
        lines.append(f"- {t['label']}: {t['count']}편, 최근 반기 {t['recent']}편 vs 이전 반기 {t['previous']}편 ({'+' if t['delta'] > 0 else ''}{t['delta']}%)")
    def block(n: int, idea: dict) -> list[str]:
        rows = [f"### {n}. {idea['title']}", ""]
        category = gap_category(idea)
        if category in GAP_CATEGORIES:
            rows.append(f"- 공백 유형: {GAP_CATEGORIES[category]['label']} — {GAP_CATEGORIES[category]['note']}")
        rows += ["", idea["rationale"], ""]
        prior = idea.get("priorArt")
        if isinstance(prior, dict) and not prior.get("error"):
            rows.append(f"- 선행연구: PubMed 전체 최근 {prior.get('years', '?')}년에서 "
                        f"같은 질문을 다룬 논문 {prior.get('matchCount', 0)}편")
            rows += [f"  - PMID {m['pmid']} ({m.get('year', '')}, {m.get('journal', '')}) {m.get('title', '')}"
                     for m in prior.get("matches", [])[:3]]
        judgment = judgment_for(idea["id"])
        if judgment:
            rows.append(f"- 판정: {VERDICT_LABEL[judgment['verdict']]}")
            rows.append(("- 판정 근거: " if judgment["verdict"] == "opportunity" else "- 추천하지 않는 이유: ")
                        + judgment.get("reason", ""))
            if judgment.get("fieldStandard"):
                rows.append(f"- 이 분야가 실제로 쓰는 1차 결과: {judgment['fieldStandard']}")
            evidence = evidence_summary(judgment, idea.get("metrics"))
            if evidence:
                rows += [f"- 판정 안정성: {evidence['stability']}", f"- 모델 간 합의: {evidence['consensus']}",
                         f"- 시간적 근거: {evidence['temporal']}", f"- 표본 충분성: {evidence['sample']}",
                         f"- 종합 근거 수준: {evidence['level']}"]
        rows += [f"- PICO: {idea['pico']}", f"- 권장 설계: {idea['design']}",
                 f"- 1차 결과: {idea['primaryEndpoint']}",
                 f"- 근거 PMID: {', '.join(e['pmid'] for e in idea['evidence'])}", ""]
        return rows

    grouped = plan_ideas(ideas)
    opportunities, structural, pending = grouped["final"], grouped["structural"], grouped["pending"]
    if opportunities or pending:
        lines += ["", "## 최종 논문 아이디어" if opportunities else "## 연구 아이디어 후보 (판정 전)", ""]
        for n, idea in enumerate(opportunities + pending, 1):
            lines += block(n, idea)
    if structural:
        lines += ["## 구조적 공백 (추천하지 않음)", "",
                  "통계적으로는 비어 있지만 그 분야의 정상적 특성이거나 이미 다뤄지는 구간입니다. "
                  "같은 주제로 연구를 설계한다면 아래 '이 분야가 실제로 쓰는 1차 결과'를 결과변수로 잡으세요.", ""]
        for n, idea in enumerate(structural, 1):
            lines += block(n, idea)
    lines.append("> 주의: 아이디어는 선택된 저널과 기간에서 관찰된 신호를 기반으로 한 후보입니다. 최종 독창성 판단 전 전체 PubMed, 임상시험 등록자료 및 선행 프로토콜 검색이 필요합니다.")
    return "\n".join(lines)


def day_label(value: str) -> str:
    """운영 로그용 월.일. compact_date는 논문 날짜용이라 월까지만 찍는다."""
    parts = (value or "").split("-")
    return f"{parts[1]}.{parts[2]}" if len(parts) == 3 else (value or "—")


def hours_since(iso: str) -> float | None:
    try:
        delta = datetime.now().astimezone() - datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
        return delta.total_seconds() / 3600
    except Exception:
        return None


def ago(hours: float) -> str:
    if hours < 1:
        return "방금"
    if hours < 48:
        return f"{round(hours)}시간 전"
    return f"{round(hours / 24)}일 전"


@st.cache_data(ttl=300, show_spinner=False)
def _trials_payload():
    return load_trials()


def render_trials_menu():
    """사이드바 임상시험 메뉴. 아이디어 파이프라인과 독립 — 요약과 진입 버튼만 둔다."""
    st.markdown("### 임상시험 레이더")
    payload = _trials_payload()
    if not payload:
        st.caption("아직 수집 전입니다. 눌러 보시면 만드는 방법이 나옵니다.")
        if st.button("임상시험 보기 →", key="trials_toggle_empty", width="stretch"):
            st.session_state.show_trials = True
            st.session_state.show_backtest = False
            st.rerun()
        return
    summary = summarize_trials(payload)
    st.caption(f"수집 {fmt_dt(payload['fetchedAt'])} · 활성 {summary['active']}건 "
               f"(인공관절 {summary['byFamily'].get('arthroplasty', 0)} · "
               f"관절경 {summary['byFamily'].get('arthroscopy', 0)}) · 결과 게시 {summary['withResults']}건")
    entry = (payload.get("history") or [{}])[0]
    changes = entry.get("changes") or {}
    moved = (changes.get("statusChangedTotal", 0) + changes.get("resultsPostedTotal", 0)
             + changes.get("newTotal", 0))
    if moved and not entry.get("firstRun"):
        st.info(f"지난 수집 이후 변동 {moved}건 — 신규 {changes.get('newTotal', 0)} · "
                f"상태 변경 {changes.get('statusChangedTotal', 0)} · "
                f"결과 게시 {changes.get('resultsPostedTotal', 0)}")
    label = "← 분석 화면으로" if st.session_state.show_trials else "임상시험 보기 →"
    if st.button(label, key="trials_toggle", width="stretch"):
        st.session_state.show_trials = not st.session_state.show_trials
        st.session_state.show_backtest = False     # 두 화면은 동시에 열지 않는다
        st.rerun()


@st.cache_data(ttl=300, show_spinner=False)
def _backtest_report():
    return latest_backtest()


def render_backtest_menu():
    """사이드바 백테스트 메뉴. 실행은 명령줄에서만 한다 — 3년치 수집이라 화면이 멈춘다."""
    st.markdown("### 백테스트")
    report = _backtest_report()
    # 결과가 없어도 버튼은 그린다. 안 그리면 메뉴가 죽은 것처럼 보이고, 어떻게
    # 만드는지 안내할 자리도 없어진다.
    if report:
        meta, summary = report.get("meta") or {}, report.get("summary") or {}
        st.caption(f"{meta.get('pastFrom', '')} ~ {meta.get('pastTo', '')} 후보로 만들어 "
                   f"{meta.get('futureTo', '')}까지 채점 · 후보 {meta.get('ideas', 0)}개 중 "
                   f"{summary.get('scored', 0)}개 채점")
    else:
        st.caption("아직 실행 전입니다. 눌러 보시면 만드는 방법이 나옵니다.")
    label = "← 분석 화면으로" if st.session_state.show_backtest else "백테스트 보기 →"
    if st.button(label, key="backtest_toggle", width="stretch"):
        st.session_state.show_backtest = not st.session_state.show_backtest
        st.session_state.show_trials = False       # 두 화면은 동시에 열지 않는다
        st.rerun()


def render_run_log():
    """사이드바 운영 로그. 수집이 매일 돌고 있는지를 한눈에 본다."""
    st.markdown("### 운영 로그")
    rows = load_run_log()
    if not rows:
        st.caption("아직 실행 기록이 없습니다. `python scripts/daily.py`가 한 번 돌면 여기에 쌓입니다.")
        return

    last = rows[0]
    hours = hours_since(last.get("at", ""))
    if not last.get("ok"):
        st.error(f"마지막 실행 실패 ({day_label(last.get('date', ''))}) — {last.get('error', '원인 미상')}")
    elif hours is not None and hours > STALE_HOURS:
        st.warning(f"마지막 수집이 {ago(hours)}입니다. 일일 작업이 멈췄을 수 있습니다.")
    else:
        st.success(f"마지막 수집 {ago(hours) if hours is not None else fmt_dt(last.get('at', ''))}"
                   f" · 초록 {last.get('analyzed', 0):,}편")

    # 최근 7일 중 며칠 기록이 있는지. 날짜 단위로 세어 하루 여러 번 돈 날을 중복으로 세지 않는다.
    recent_days = {r.get("date") for r in rows if r.get("ok")}
    week = [(date.today() - timedelta(days=n)).isoformat() for n in range(7)]
    hit = sum(1 for d in week if d in recent_days)
    fails = sum(1 for r in rows[:7] if not r.get("ok"))
    line = f"최근 7일 중 {hit}일 수집" + (f" · 실패 {fails}건" if fails else "")
    ai_runs = [r for r in rows if r.get("ok") and (r.get("ai") or {}).get("ran")]
    if ai_runs:
        line += f" · AI 갱신 {day_label(ai_runs[0].get('date', ''))}"
    st.caption(line)

    with st.expander(f"최근 실행 {min(len(rows), RUN_LOG_ROWS)}건"):
        table = []
        for r in rows[:RUN_LOG_ROWS]:
            ai = r.get("ai") or {}
            table.append({
                "날짜": day_label(r.get("date", "")),
                "초록": r.get("analyzed") if r.get("ok") else None,
                "공백": r.get("ideas") if r.get("ok") else None,
                "AI": ("갱신" if ai.get("ran") else "재사용") if r.get("ok") else "—",
                "상태": "정상" if r.get("ok") else "실패",
            })
        st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")
        note = last.get("ai") or {}
        if note.get("reason"):
            st.caption(f"마지막 AI 판단: {note['reason']}"
                       + (f" · 판정 {note.get('judged', 0)}건 · 제안 {note.get('suggested', 0)}건" if note.get("ran") else ""))
        if note.get("failed"):
            st.caption(f"AI 호출 실패 {note['failed']}건이 섞여 있습니다.")
        if last.get("capped"):
            st.caption("수집 상한에 걸렸습니다. 기간을 좁히거나 저널을 줄이면 더 촘촘히 봅니다.")


def section(num: str, title: str, note: str = ""):
    st.markdown(f'<div class="sec"><i>{num}</i><h2>{title}</h2><p>{note}</p></div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# 화면: 헤더 + 질의 패널
# ---------------------------------------------------------------------------

require_login()
init_state()

st.markdown('<div class="eyebrow">ORTHOPAEDIC INTELLIGENCE</div>', unsafe_allow_html=True)
st.markdown('<h1 class="hero-title">지난 1년의 초록에서<br><em>다음 논문</em>을 찾습니다.</h1>', unsafe_allow_html=True)
st.markdown(f'<p class="hero-text">{len(JOURNALS)}개 핵심 저널의 PubMed 초록에서 무릎 논문만 추려, 연구 신호와 빈틈을 근거 논문까지 연결해 보여주는 정형외과 연구 레이더입니다.</p>', unsafe_allow_html=True)

with st.sidebar:
    st.markdown("### 분석 조건")
    selected: list[str] = []
    for fam in FAMILY_ORDER:
        members = [k for k in JOURNAL_ORDER if JOURNALS[k]["family"] == fam]
        st.caption(FAMILIES[fam]["label"])
        cols = st.columns(2)
        for n, key in enumerate(members):
            if cols[n % 2].checkbox(JOURNALS[key]["short"], value=True, key=f"j_{key}", help=JOURNALS[key]["label"]):
                selected.append(key)
    today = date.today()
    date_from = st.date_input("시작일", value=today - timedelta(days=365), max_value=today)
    date_to = st.date_input("종료일", value=today, min_value=date_from, max_value=today)
    focus = st.text_input("연구 초점 (선택)", placeholder="예: UKA conversion, rotator cuff, PROM")
    run_clicked = st.button("분석 시작 ↗", type="primary", width="stretch")
    creds = credentials()
    cached_enhancements = len(cache_load())
    st.caption("NCBI E-utilities 기반 · 검색된 초록 전량 수집"
               + (" · API 키 적용" if creds.api_key else "")
               + (" · Gemini 고도화 가능" if secret("GEMINI_API_KEY") else " · GEMINI_API_KEY 없음")
               + (f" · 고도화 결과 {cached_enhancements}건 재사용 대기" if cached_enhancements else ""))
    st.divider()
    render_run_log()
    st.divider()
    render_trials_menu()
    st.divider()
    render_backtest_menu()
    st.divider()
    if st.session_state.snapshot:
        snap = st.session_state.snapshot
        st.info(f"일일 스냅샷 표시 중 ({fmt_dt(snap['generatedAt'])}). 조건을 바꿔 분석하면 실시간 결과로 전환됩니다.")
        # 초록은 매일, AI 분석은 주 1회 갱신된다. 둘의 시점이 다르므로 명시한다.
        if snap.get("aiRefreshedAt") and snap["aiRefreshedAt"] != snap["generatedAt"]:
            st.caption(f"AI 동향·제안은 {fmt_dt(snap['aiRefreshedAt'])} 기준입니다 (주 1회 금요일 갱신). 초록은 매일 갱신됩니다.")

# 첫 진입: 스냅샷이 있으면 바로, 없으면 기본 1년 범위로 실시간 분석.
if not st.session_state.booted:
    st.session_state.booted = True
    snap = load_snapshot()
    if snap:
        st.session_state.snapshot = snap
        st.session_state.analysis = snap["analysis"]
        st.session_state.scope = default_scope(snap)
    else:
        with st.spinner("네 개 저널의 신호를 읽고 있습니다 — 검색 → 초록 구조화 → 주제 분류 → 연구 공백 조합"):
            run(selected, (today - timedelta(days=365)).isoformat(), today.isoformat(), "")

if run_clicked:
    if not selected:
        st.session_state.error = "분석할 저널을 한 개 이상 선택해 주세요."
    else:
        run(selected, date_from.isoformat(), date_to.isoformat(), focus)

if st.session_state.error:
    st.error(st.session_state.error)

# --- 백테스트 화면 ----------------------------------------------------------
# 결과 파일만 읽어 보여준다. 실행은 명령줄에서만 한다 — 3년치 수집이라 화면이 멈춘다.
# 임상시험 화면과 같은 이유로 "분석 결과 없음" st.stop()보다 앞에 둔다.
if st.session_state.show_backtest:
    report = _backtest_report()
    if not report:
        section("01", "백테스트", "아직 실행하지 않았습니다")
        st.markdown("""
시계를 과거로 돌려 **그때의 초록만으로** 아이디어를 만들고, 그 이후에 실제로 그 공백이
채워졌는지 채점합니다. 탐지기와 판정기가 미래를 예측하는지 숫자로 확인하는 유일한 방법입니다.

앱에서는 돌리지 않습니다 — 3년치 PubMed 수집이라 화면이 몇 분씩 멈춥니다. 터미널에서
아래를 실행하시면 이 화면에 결과가 채워집니다.

**1단계 · 무료** — 탐지기 자체가 미래를 예측하는가
```
python scripts/backtest.py
```

**2단계 · 후보당 약 110원** — 판정기가 기회와 구조적 공백을 실제로 가르는가
```
python scripts/backtest.py --judge
```

판정은 캐시되므로 같은 조건으로 다시 돌리면 무료입니다. NCBI·Gemini 키는
`.streamlit/secrets.toml`에서 자동으로 읽습니다.
""")
        st.caption("창을 바꾸려면 `--past-from 2023-01-01 --past-to 2023-12-31` 처럼 지정하고, "
                   "채점 표본을 늘리려면 `--candidates 40`을 씁니다. "
                   "결과는 `data/backtest/`에 JSON과 마크다운으로 함께 저장됩니다.")
        st.stop()
    meta = report.get("meta") or {}
    summary = report.get("summary") or {}
    outcomes = report.get("outcomes") or []

    section("01", "백테스트", f"{report.get('path', '')} · 생성 {meta.get('generatedAt', '')[:16]}")
    st.caption("시계를 과거로 돌려 그때의 초록만으로 아이디어를 만들고, 그 이후에 실제로 "
               "그 공백이 채워졌는지 채점한 결과입니다. 탐지기와 판정기가 미래를 예측하는지 "
               "숫자로 확인하는 유일한 방법입니다. 실행은 명령줄에서 합니다 — "
               "`python scripts/backtest.py`.")

    b1, b2, b3, b4 = st.columns(4)
    b1.metric("후보", f"{meta.get('ideas', 0)}개")
    b2.metric("채점", f"{summary.get('scored', 0)}개")
    b3.metric("건너뜀", f"{len(summary.get('skipped') or [])}개")
    b4.metric("판정 포함", f"{meta.get('judged', 0)}개")
    st.caption(f"과거 창 {meta.get('pastFrom', '')} ~ {meta.get('pastTo', '')} "
               f"(초록 {meta.get('pastArticles', 0):,}편) → "
               f"미래 창 {meta.get('futureFrom', '')} ~ {meta.get('futureTo', '')} "
               f"(초록 {meta.get('futureArticles', 0):,}편)")

    section("02", "판정별 채움률", "opportunity가 structural보다 뚜렷이 높아야 판정이 작동하는 것입니다")
    rows = []
    for verdict, row in (summary.get("byVerdict") or {}).items():
        n = row["count"] or 1
        rows.append({
            "판정": VERDICT_LABEL.get(verdict, verdict).split(" —")[0],
            "건수": row["count"],
            "기준 A (공백 해소)": f"{row['filledByRatio']} ({round(row['filledByRatio'] / n * 100)}%)",
            f"기준 B (직접 논문 {BACKTEST_MIN_PAPERS}편+)": f"{row['filledByCount']} ({round(row['filledByCount'] / n * 100)}%)",
            "둘 중 하나": f"{row['filledEither']} ({round(row['filledEither'] / n * 100)}%)",
        })
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    else:
        st.caption("채점된 항목이 없습니다.")
    if summary.get("scored", 0) < 10:
        st.warning(f"채점된 공백이 {summary.get('scored', 0)}개뿐입니다. 경향을 보는 용도이지 "
                   "통계적 근거가 아닙니다 — `--candidates`를 올리거나 창을 넓혀 다시 돌리세요.")
    st.caption("기준 A는 같은 탐지 조건으로 다시 재서 공백이 사라졌고 그 이유가 기준선 하락이 "
               "아니라 비율 상승인 경우입니다. 기준 B는 절대 편수라 큰 클러스터에 유리하므로, "
               "아래 표의 `기대 편수`(과거 비율 × 미래 표본)와 견줘 읽으세요.")

    section("03", "공백별 상세", f"{len(outcomes)}개")
    detail = []
    for o in outcomes:
        detail.append({
            "클러스터 × 공백": f"{o.get('cluster', '')} × {o.get('gapId', '')}",
            "판정": VERDICT_LABEL.get(o.get("verdict", ""), o.get("verdict", "")).split(" —")[0],
            "과거 비율": f"{round((o.get('pastRatio') or 0) * 100, 1)}%",
            "미래 비율": ("—" if o.get("skipped") else f"{round((o.get('futureRatio') or 0) * 100, 1)}%"),
            "직접 논문": ("—" if o.get("skipped") else o.get("futureObserved")),
            "기대 편수": ("—" if o.get("skipped") else o.get("expectedAtPastRate")),
            "기준 A": ("—" if o.get("skipped") else ("충족" if o.get("filledByRatio") else "")),
            "기준 B": ("—" if o.get("skipped") else ("충족" if o.get("filledByCount") else "")),
            "비고": o.get("skipped") or o.get("ratioNote", ""),
        })
    st.dataframe(pd.DataFrame(detail), hide_index=True, width="stretch")
    st.stop()   # 백테스트 화면에서는 아래 분석 화면을 그리지 않는다


# --- 임상시험 레이더 화면 --------------------------------------------------
# 아이디어 파이프라인과 독립이라 분석 결과가 없어도 열려야 한다. 그래서 아래
# "분석 결과 없음" st.stop()보다 앞에 둔다.
if st.session_state.show_trials:
    payload = _trials_payload()
    if not payload:
        st.info("임상시험 자료가 아직 없습니다. `python scripts/trials_daily.py`를 먼저 실행해 주세요.")
        st.stop()
    trials = list(payload["trials"].values())
    tsummary = summarize_trials(payload)

    section("01", "임상시험 레이더", f"ClinicalTrials.gov · 수집 {fmt_dt(payload['fetchedAt'])}")
    st.caption("이 화면은 논문 아이디어 생성과 무관한 독립 관찰 도구입니다. "
               "무릎 인공관절·관절경 관련 등록 시험의 모습과 변동을 추적합니다. "
               "출판 전 경쟁 연구를 미리 보는 창이기도 합니다 — 모집 종료·완료 전환은 "
               "보통 1~2년 안에 논문이 나온다는 신호입니다.")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("전체", f"{tsummary['total']}건")
    m2.metric("활성 (모집·진행)", f"{tsummary['active']}건")
    m3.metric("완료", f"{tsummary['completed']}건")
    m4.metric("결과 게시", f"{tsummary['withResults']}건")

    entry = (payload.get("history") or [{}])[0]
    changes = entry.get("changes") or {}
    if entry.get("firstRun"):
        st.info("첫 수집입니다. 변동 감지는 다음 수집부터 시작됩니다.")
    else:
        section("02", "최근 변동", "지난 수집과의 비교")
        rows = []
        for c in changes.get("statusChanged", []):
            rows.append({"종류": "상태 변경", "NCT": c["nctId"], "제목": c["title"][:80],
                         "내용": f"{STATUS_LABEL.get(c['from'], c['from'])} → {STATUS_LABEL.get(c['to'], c['to'])}"})
        for c in changes.get("resultsPosted", []):
            rows.append({"종류": "결과 게시", "NCT": c["nctId"], "제목": c["title"][:80], "내용": "결과 데이터 공개됨"})
        for c in changes.get("new", []):
            rows.append({"종류": "신규 등록", "NCT": c["nctId"], "제목": c["title"][:80],
                         "내용": STATUS_LABEL.get(c["status"], c["status"])})
        for c in changes.get("completionMoved", []):
            rows.append({"종류": "완료일 이동", "NCT": c["nctId"], "제목": c["title"][:80],
                         "내용": f"{c['from']} → {c['to']}"})
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        else:
            st.caption("변동 없음.")
        if changes.get("gone"):
            st.caption(f"검색 범위에서 빠진 시험 {changes['gone']}건 (대부분 검색어 경계의 흔들림입니다).")

    section("03", "시험 목록", f"{len(trials)}건")
    f1, f2 = st.columns(2)
    family_pick = f1.radio("계열", ["전체", "인공관절", "관절경·스포츠"], horizontal=True, key="trials_family")
    status_pick = f2.radio("상태", ["활성만", "완료", "결과 게시", "전체"], horizontal=True, key="trials_status")
    # 검색이 전체 필드를 훑어서 무릎과 무관한 시험이 15%쯤 섞인다. 버리지 않고
    # 표시만 해 두었으므로(radar/trials.py의 KNEE_PATTERN), 끄고 켤 수 있게 둔다.
    knee_only = st.checkbox("제목·질환명에 무릎이 명시된 것만 "
                            f"({tsummary['kneeExplicit']}/{tsummary['total']}건)",
                            key="trials_knee_only")

    def _visible(t):
        if knee_only and not t.get("kneeExplicit"):
            return False
        if family_pick != "전체":
            key = next((k for k, v in TRIAL_FAMILY_LABEL.items() if v == family_pick), "")
            if key not in t.get("families", []):
                return False
        if status_pick == "활성만":
            return t.get("status") in ACTIVE_STATUSES
        if status_pick == "완료":
            return t.get("status") == "COMPLETED"
        if status_pick == "결과 게시":
            return bool(t.get("hasResults"))
        return True

    visible = sorted((t for t in trials if _visible(t)),
                     key=lambda t: t.get("lastUpdated") or "", reverse=True)
    st.caption(f"{len(visible)}건 표시 · 최근 갱신 순")
    st.dataframe(pd.DataFrame([{
        "NCT": t["nctId"],
        "제목": t["title"][:90],
        "상태": STATUS_LABEL.get(t["status"], t["status"]),
        "계열": " · ".join(TRIAL_FAMILY_LABEL.get(f, f) for f in t.get("families", [])),
        "목표 인원": t.get("enrollment"),
        "1차 완료 예정": t.get("primaryCompletionDate") or "—",
        "결과": "게시" if t.get("hasResults") else "",
        "링크": trial_url(t["nctId"]),
    } for t in visible[:200]]), hide_index=True, width="stretch",
        column_config={"링크": st.column_config.LinkColumn("링크", display_text="열기")})
    if len(visible) > 200:
        st.caption("200건까지만 표시합니다. 필터를 좁혀 주세요.")
    st.stop()   # 임상시험 화면에서는 아래 분석 화면을 그리지 않는다


analysis = st.session_state.analysis
if not analysis:
    st.info("왼쪽에서 조건을 정하고 **분석 시작**을 눌러 주세요.")
    st.stop()

# ---------------------------------------------------------------------------
# 01 분석 개요
# ---------------------------------------------------------------------------

if analysis.get("capped"):
    st.warning(f"**표본 축소** — 검색된 {analysis['collected']:,}편 중 {analysis['cap']:,}편만 기간·저널에 걸쳐 균등하게 골라 분석했습니다 "
               f"(전체의 {round(analysis['cap'] / analysis['collected'] * 100)}%). 아래의 편수는 축소된 값이라 다른 기간과 직접 비교할 수 없습니다. "
               "비중·변화율·상승 판정은 그대로 유효합니다.")
if analysis.get("unknownJournals"):
    st.caption("인식하지 못해 제외한 저널명: " + ", ".join(analysis["unknownJournals"]))

section("01", "분석 개요", f"업데이트 {fmt_dt(analysis['generatedAt'])}")
m1, m2, m3, m4 = st.columns([1.2, 1, 1, 1.5])
m1.metric("분석 초록", f"{analysis['analyzed']}편")
m1.caption(f"검색 결과 {analysis['totalAvailable']:,}편 중 초록 보유 {analysis['withAbstract']:,}편 · 무릎 논문만 분석 "
           f"(관절 미특정 {analysis['excludedMultiJoint']:,}편, 타 관절 {analysis['excludedOtherJoints']:,}편 제외)")
m2.metric("초록 수록률", f"{analysis['abstractCoverage']}%")
m2.caption("제목만 있는 문헌은 트렌드 계산에서 제외")
m3.metric("상승 주제", f"{sum(1 for t in analysis['trends'] if t['signal'] == 'rising')}개")
m3.caption("직전 반기 대비 출판 비중 증가")
with m4:
    st.caption("저널 구성")
    jdf = pd.DataFrame([{"저널": JOURNALS[j["key"]]["short"], "편수": j["count"]} for j in analysis["journals"]])
    st.bar_chart(jdf.set_index("저널"), height=170, color="#10231f")

# 분석 범위 선택
scope_options: list[tuple[str, tuple]] = [(f"전체 {analysis['analyzed']}", ("all", None))]
for fam in analysis["families"]:
    if fam["key"] in analysis["trendsByFamily"]:
        scope_options.append((f"{fam['short']} {fam['count']}", ("family", fam["key"])))
for fam in analysis["families"]:
    for key in fam["journals"]:
        j = next((j for j in analysis["journals"] if j["key"] == key), None)
        if j:
            scope_options.append((f"{JOURNALS[key]['short']} {j['count']}", ("journal", key)))
labels = [o[0] for o in scope_options]
current = next((i for i, o in enumerate(scope_options) if o[1] == tuple(st.session_state.scope)), 0)
picked = st.radio("분석 범위", labels, index=current, horizontal=True, key="scope_radio")
new_scope = scope_options[labels.index(picked)][1]
if tuple(new_scope) != tuple(st.session_state.scope):
    st.session_state.scope = new_scope
    st.session_state.active_topic = "전체"
    st.session_state.visible_articles = ARTICLE_PAGE

active_trends, scope_label, scope_count = scope_info(analysis)
kind, key = st.session_state.scope

# ---------------------------------------------------------------------------
# 02 Research signals
# ---------------------------------------------------------------------------

section("02", "Research signals", f"{scope_label} · 기간 후반부와 전반부의 초록 비중을 비교했습니다.")

snap = st.session_state.snapshot
scope_report = (snap or {}).get("trendReports", {}).get(key) if kind == "family" else None
if scope_report and not scope_report.get("error"):
    html = [f'<div class="ai"><div class="lbl">AI 동향 분석 · {esc(scope_report.get("model"))}</div>',
            f"<h3>{esc(scope_report.get('headline'))}</h3><p>{esc(scope_report.get('summary'))}</p>"]
    if scope_report.get("movements"):
        html.append("<ul>")
        for mv in scope_report["movements"]:
            links = " ".join(f'<a href="{pubmed(esc(p))}" target="_blank">{esc(p)}</a>' for p in mv.get("evidence", []))
            html.append(f"<li><b>{esc(mv['topic'])}</b> {esc(mv['reading'])} <small>{links}</small></li>")
        html.append("</ul>")
    if scope_report.get("watchList"):
        html.append(f"<p><b>지켜볼 것</b> {' · '.join(esc(w) for w in scope_report['watchList'])}</p>")
    st.markdown("".join(html) + "</div>", unsafe_allow_html=True)

left, right = st.columns([2, 1])
with left:
    ranked = [(n + 1, t) for n, t in enumerate(active_trends)]
    head = ranked[:TREND_ROWS]
    rising_tail = [r for r in ranked[TREND_ROWS:] if r[1]["signal"] == "rising"]
    rows = head + rising_tail
    if rows:
        tdf = pd.DataFrame([{
            "순위": f"{n:02d}", "주제": t["label"], "편수": t["count"],
            "변화": f"{'+' if t['delta'] > 0 else ''}{t['delta']}%",
            "신호": signal_label(t["signal"]), "비중": t["share"],
        } for n, t in rows])
        st.dataframe(tdf, hide_index=True, width="stretch",
                     column_config={"비중": st.column_config.ProgressColumn("비중", format="%d%%", min_value=0, max_value=100)})
        if rising_tail:
            st.caption(f"편수 {TREND_ROWS}위 밖의 상승 신호 {len(rising_tail)}개를 아래에 덧붙였습니다.")
    else:
        st.info("이 범위에는 트렌드를 계산할 초록이 없습니다.")
with right:
    top_rising = next((t for t in active_trends if t["signal"] == "rising"), None)
    if top_rising:
        headline = f"{top_rising['label']}가 가장 뚜렷하게 늘고 있습니다."
        body = ("표는 <b>편수 순</b>입니다. 편수가 많다고 새 질문이 있는 것은 아니니, 큰 주제는 성숙도로, <b>변화율</b>은 방향으로 읽으세요. "
                "상승 주제를 그대로 반복하기보다 임상에서 중요하지만 함께 다뤄지지 않은 환자군·결과변수·연구설계를 결합하는 것이 핵심입니다.")
    else:
        headline = "뚜렷한 상승 신호가 없습니다."
        body = "이 범위에서는 표본이 충분한 주제 중 상승으로 판정된 것이 없습니다. 기간을 넓히거나 저널 선택을 바꿔 다시 보세요."
    st.markdown(f'<div class="insight"><div class="lbl">RADAR NOTE</div><h3>{headline}</h3><p>{body}</p>'
                f'<div class="formula">좋은 아이디어 = 상승 신호 × 미충족 질문 × 가능한 데이터</div></div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# 03 추천 연구기회 / 04 구조적 공백
#
# 탐지된 공백을 전부 "추천 아이디어"로 내놓으면, 실제로는 그 분야의 정상적 특성인
# 공백(예: 감염 연구에 PROM이 적은 것)까지 추천으로 읽힌다. 판정 결과에 따라 자리를
# 나누되 지우지는 않는다. 추천하지 않는 공백도 "왜 아닌지"와 그 분야의 1차 결과를
# 함께 보여주면, 그 자체가 주제 선정에 쓸모 있는 정보다.
# ---------------------------------------------------------------------------

ideas, fell_back = active_ideas(analysis)
saved = st.session_state.saved_ideas
show_saved = st.session_state.show_saved and bool(saved)

if show_saved:
    note = f"저장한 아이디어 {len(saved)}개 · data/saved_ideas.json에 보관됩니다"
elif kind == "all" or fell_back:
    note = f"선택한 저널 전체 {analysis['analyzed']:,}편 기준" + (f" · {scope_label} 단독으로는 상승 신호가 부족합니다" if fell_back else "")
else:
    note = f"{scope_label} {scope_count:,}편 기준"

shown = saved if show_saved else ideas
plan = None if show_saved else plan_ideas(shown)
# 모든 범위를 판정하지는 않는다(호출 비용). 판정 블록이 통째로 비면 이유를 모른 채
# "판정이 실패했나" 싶으므로, 빈 화면 대신 그렇게 설계됐다고 밝히고 어디로 가면
# 되는지 알려준다. 판정하는 범위는 야간 작업 설정에 따라 바뀌므로 스냅샷을 보고 정한다.
if not show_saved and st.session_state.snapshot and not _scoped("judgments"):
    stored = st.session_state.snapshot.get("judgments") or {}
    where = ["무릎 전체"] if stored.get("all") else []
    where += [FAMILIES[f]["short"] for f in FAMILY_ORDER if stored.get(f)]
    if where:
        st.caption(f"이 목록에는 AI 판정·고도화가 없습니다 — 판정은 **{' · '.join(where)}** 범위에서만 돌립니다. "
                   "판정 근거와 고도화된 계획서를 보시려면 위에서 그 범위를 선택하세요.")
gemini_key = secret("GEMINI_API_KEY")
gemini_model = resolve_model(secret("GEMINI_MODEL"))


def render_idea(n: int, idea: dict):
    with st.container(border=True):
        top_l, top_r = st.columns([3, 1])
        category = gap_category(idea)
        badge = (f'<span class="gapcat">{GAP_CATEGORIES[category]["label"]}</span>'
                 if category in GAP_CATEGORIES else '<span class="gapcat">분류 없음</span>')
        subtype = idea.get("gapSubtype")
        if subtype in LONGTERM_SUBTYPES:
            badge += f'<span class="tag" title="{LONGTERM_SUBTYPES[subtype]}">{subtype}</span>'
        top_l.markdown(f"**{n:02d}** &nbsp; " + badge
                       + "".join(f'<span class="tag">{t}</span>' for t in idea["tags"]), unsafe_allow_html=True)
        top_r.markdown(f"<div style='text-align:right;font-size:12px'>독창성 <b>{idea['novelty']}/5</b> · 실현성 <b>{idea['feasibility']}/5</b></div>", unsafe_allow_html=True)
        st.markdown(f"#### {idea['title']}")
        st.write(idea["rationale"])
        block = verdict_html(idea)
        if block:
            st.markdown(block, unsafe_allow_html=True)
        score = plan["scores"].get(idea["id"]) if plan else None
        if score:
            st.markdown(scores_html(score), unsafe_allow_html=True)
        st.markdown(f"- **연구대상·비교군** {idea['pico']}\n- **권장 설계** {idea['design']}\n"
                    f"- **1차 결과변수** {idea['primaryEndpoint']}")
        st.markdown("신호 근거 · " + " · ".join(f"[PMID {e['pmid']}]({pubmed(e['pmid'])})" for e in idea["evidence"]))
        block = prior_art_html(idea)
        if block:
            st.markdown(block, unsafe_allow_html=True)

        c1, c2, _ = st.columns([1, 1, 3])
        saved_now = is_saved(idea["id"])
        if c1.button(("★ 저장됨" if saved_now else "☆ 저장"), key=f"save_{idea['id']}", width="stretch"):
            toggle_saved(idea, scope_label)
            st.rerun()
        if not show_saved:
            # 판정과 무관하게 열어 둔다. 판정은 참고 의견이고, 반대해 보고 싶은 사람도 있다.
            redo = suggestion_for(idea["id"]) is not None
            label = "✦ 다시 고도화" if redo else "✦ AI로 고도화"
            if c2.button(label, key=f"enh_{idea['id']}", width="stretch", disabled=not gemini_key,
                         help=None if gemini_key else "GEMINI_API_KEY를 설정하면 사용할 수 있습니다."):
                pool = [a for a in analysis["articles"] if in_scope(a)]
                pmids = pmids_for_idea(idea, pool, analysis["trends"])
                if len(pmids) < ENHANCE_MIN:
                    st.session_state.enhance_error[idea["id"]] = f"근거 초록이 {len(pmids)}편뿐이라 고도화할 수 없습니다."
                else:
                    # 세션 상태는 새로고침 한 번에 사라진다. 그래서 같은 카드를 다시 눌러도
                    # Gemini가 다시 불렸다. 열쇠(아이디어·범위·근거 초록·모델)가 같으면 답도
                    # 같으니 파일에서 꺼내 쓴다. "다시 고도화"는 새 답을 보려고 누르는 것이라
                    # 그때만 건너뛴다.
                    ck = cache_key("idea", idea["id"], scope_label, pmids, gemini_model)
                    hit = None if redo else cache_get(ck)
                    if hit:
                        st.session_state.enhanced[idea["id"]] = {**hit, "fromCache": True}
                        st.session_state.enhance_error.pop(idea["id"], None)
                    else:
                        with st.spinner("근거 초록을 읽고 Gemini가 구체화하는 중…"):
                            try:
                                result = enhance_idea(
                                    idea, pmids, active_trends, scope_label, f"{analysis['dateFrom']}–{analysis['dateTo']}",
                                    credentials(), gemini_key, gemini_model)
                                cache_put(ck, result)
                                st.session_state.enhanced[idea["id"]] = result
                                st.session_state.enhance_error.pop(idea["id"], None)
                            except Exception as error:
                                st.session_state.enhance_error[idea["id"]] = str(error) or "고도화 중 오류가 발생했습니다."
                st.rerun()

        if st.session_state.enhance_error.get(idea["id"]):
            st.error(st.session_state.enhance_error[idea["id"]])
        sug = suggestion_for(idea["id"])
        if sug and sug.get("error"):
            st.error(f"AI 제안 없음 — {sug['error']}")
        elif sug:
            meta = f"**AI 제안** · {sug.get('model')} · 초록 {sug.get('abstractsUsed')}편 참조"
            if sug.get("fromCache"):
                meta += " · 저장된 결과 재사용 (호출 없음)"
            if sug.get("droppedCitations"):
                meta += f" · 목록에 없던 인용 {sug['droppedCitations']}건 제거"
            if idea["id"] not in st.session_state.enhanced and snap:
                meta += f" · {fmt_dt(snap['generatedAt'])} 생성"
            with st.expander("AI 제안 보기", expanded=True):
                st.caption(meta)
                st.markdown(f"**연구 질문**  \n{sug.get('question', '')}")
                if sug.get("pico"):
                    pico = sug["pico"]
                    st.markdown(f"**PICO**  \n- P: {pico.get('population', '')}\n- I: {pico.get('intervention', '')}\n"
                                f"- C: {pico.get('comparison', '')}\n- O: {pico.get('outcome', '')}")
                st.markdown(f"**선행연구 공백**  \n{sug.get('gap', '')}")
                st.markdown(f"**권장 설계**  \n{sug.get('design', '')}")
                if sug.get("limitations"):
                    st.markdown("**예상 한계**  \n" + "\n".join(f"- {x}" for x in sug["limitations"]))
                if sug.get("evidence"):
                    st.markdown("**근거 PMID**  \n" + "\n".join(f"- [PMID {e['pmid']}]({pubmed(e['pmid'])}) {e.get('note', '')}" for e in sug["evidence"]))


section("03", "저장한 아이디어" if show_saved else "최종 논문 아이디어", note)

b1, b2, b3 = st.columns([1, 1, 4])
if saved:
    if b1.button("← 분석 결과로" if show_saved else f"★ 저장한 아이디어 {len(saved)}", width="stretch"):
        st.session_state.show_saved = not st.session_state.show_saved
        st.rerun()
if show_saved:
    b2.download_button("저장 목록 내려받기 ↓", saved_markdown(), f"arthroscope-saved-{date.today().isoformat()}.md",
                       "text/markdown", width="stretch")
else:
    b2.download_button("보고서 내려받기 ↓", report_markdown(analysis, active_trends, scope_label, scope_count, ideas, fell_back),
                       f"arthroscope-{analysis['dateTo']}.md", "text/markdown", width="stretch")

if not shown:
    st.info("이 범위에서 탐지된 연구 공백이 없습니다. 공백이 없으면 아이디어도 만들지 않습니다.")
elif show_saved:
    for n, idea in enumerate(shown, 1):
        render_idea(n, idea)
else:
    if plan["final"]:
        st.caption(f"후보 {len(shown)}개에서 구조적 공백과 최소 통과점수({config.MIN_FINAL_SCORE}점) 미달을 빼고, "
                   f"클러스터×공백이 같거나 문장 구조가 같은 것을 합친 뒤, "
                   f"전역 제약(카테고리별 최대 {config.MAX_PER_GAP_CATEGORY}개 · PROM 최대 {config.MAX_PROM_IDEAS}개) 아래 "
                   f"{plan.get('combinationsChecked', 0):,}가지 조합을 모두 계산해 최고점을 골랐습니다 — "
                   f"{len(plan['final'])}개 · 서로 다른 공백 유형 {plan['distinctCategories']}종."
                   + (" 자격을 통과한 후보가 부족해 목표보다 적습니다." if plan["shortOfTarget"] else ""))
        for n, idea in enumerate(plan["final"], 1):
            render_idea(n, idea)
    elif plan["structural"]:
        st.info("이번 범위에서는 최종 아이디어로 올릴 공백이 없습니다. 탐지된 공백이 모두 구조적으로 판정됐습니다. "
                "판정에 동의하지 않는다면 아래 카드에서 그대로 저장하거나 고도화할 수 있습니다.")
    # 실시간 분석에는 판정이 없다(판정은 일일 스냅샷에서만 돈다). 없는 판정을 있는 척하지
    # 않고, 판정 전 후보로 같은 자리에 둔다.
    for n, idea in enumerate(plan["pending"], len(plan["final"]) + 1):
        render_idea(n, idea)
    if plan["pending"] and not plan["final"]:
        st.caption("일일 스냅샷이 아닌 실시간 분석 결과라 공백 판정과 선행연구 검증이 아직 없습니다. "
                   "위 목록은 통계가 찾아낸 공백 그대로이며, 구조적 공백이 섞여 있을 수 있습니다.")

    if plan["structural"]:
        section("04", "구조적 공백", f"{len(plan['structural'])}개 · 통계적으로는 비어 있지만 그 분야의 정상적 특성")
        st.caption("삭제하지 않고 남겨 둡니다. 왜 추천하지 않았는지와 그 분야가 실제로 쓰는 1차 결과를 함께 보면, "
                   "같은 주제로 연구를 설계할 때 무엇을 결과변수로 잡아야 하는지가 드러납니다.")
        for n, idea in enumerate(plan["structural"], 1):
            render_idea(n, idea)

    if plan.get("provisional"):
        section("05", "검증 대기", f"{len(plan['provisional'])}개 · 선행연구 검증이 없어 독창성을 측정하지 못한 후보")
        st.caption("공백 자체는 확인됐지만, 같은 연구가 이미 있는지 확인하지 못했습니다. "
                   "독창성을 임의로 채우지 않고 최종에서 뺐습니다 — 선행연구 검증이 끝나면 후보로 돌아옵니다.")
        for n, idea in enumerate(plan["provisional"], 1):
            render_idea(n, idea)

    if plan["duplicates"]:
        with st.expander(f"검토 대상 {len(plan['duplicates'])}개 — 의미가 겹치거나 제약에서 밀린 후보"):
            st.caption("같은 공백 틀에서 나와 연구 질문·설계·1차 결과의 구조가 겹치거나, 판정이 uncertain이거나, "
                       "전역 제약에 걸려 최종에서 빠진 후보입니다. 최종 목록의 대안으로 볼 수 있습니다.")
            for n, idea in enumerate(plan["duplicates"], 1):
                render_idea(n, idea)

st.markdown(f'<div class="caveat"><b>중요 · ‘아이디어 후보’이지 독창성 확정 판정은 아닙니다.</b> 선택한 {len(analysis["journals"])}개 저널 밖의 PubMed 전체 검색, '
            "ClinicalTrials.gov/WHO ICTRP, PROSPERO 및 학회 초록을 확인한 뒤 연구계획서로 발전시키세요.</div>", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# 06 근거 초록
# ---------------------------------------------------------------------------

topic_options = ["전체"] + [t["label"] for t in active_trends[:5]]
if st.session_state.active_topic not in topic_options and st.session_state.active_topic != "전체":
    topic_options.append(st.session_state.active_topic)
filtered = [a for a in analysis["articles"] if in_scope(a)
            and (st.session_state.active_topic == "전체" or st.session_state.active_topic in a["topics"])]
section("06", "근거 초록", f"{len(filtered)}편 표시 가능")

picked_topic = st.radio("주제", topic_options, index=topic_options.index(st.session_state.active_topic), horizontal=True, key="topic_radio")
if picked_topic != st.session_state.active_topic:
    st.session_state.active_topic = picked_topic
    st.session_state.visible_articles = ARTICLE_PAGE
    st.rerun()

if not filtered:
    st.info("선택한 조건에 맞는 초록이 없습니다.")
for a in filtered[:st.session_state.visible_articles]:
    st.markdown(
        f'<div class="article"><div class="meta"><b>{JOURNALS[a["journalKey"]]["short"]}</b> · {compact_date(a["date"])} · {a["joint"]} · {a["design"]}</div>'
        f'<h4><a href="{pubmed(a["pmid"])}" target="_blank">{esc(a["title"])}</a></h4><p>{esc(a["abstract"])}</p>'
        f'<div class="authors">{esc(a["authors"])} &nbsp; ' + " ".join(f"#{t}" for t in a["topics"][:3]) + "</div></div>",
        unsafe_allow_html=True)
if st.session_state.visible_articles < len(filtered):
    if st.button(f"초록 더 보기 +{ARTICLE_PAGE}"):
        st.session_state.visible_articles += ARTICLE_PAGE
        st.rerun()

st.divider()
st.caption("ArthroScope · 근거를 읽고, 빈틈을 찾고, 다음 연구를 설계합니다. · Data: PubMed / NCBI E-utilities")
