"""ArthroScope Research Radar — Streamlit 앱 (app/page.tsx 포팅).

실행: streamlit run app.py
"""
from __future__ import annotations

import hmac
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

from radar.analysis import FAMILIES, FAMILY_ORDER, JOURNAL_ORDER, JOURNALS, AnalysisError, run_analysis
from radar.gemini import ENHANCE_MIN, GEMINI_DEFAULT_MODEL, enhance_idea, pmids_for_idea
from radar.ncbi import NcbiCredentials

TREND_ROWS = 9          # 편수 순 표에서 먼저 보여주는 행 수. 그 아래 상승 신호는 따로 덧붙인다.
ARTICLE_PAGE = 8
SNAPSHOT_PATH = Path("data/daily.json")
SAVED_PATH = Path("data/saved_ideas.json")   # localStorage 대신 로컬 파일에 저장

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
        if hmac.compare_digest(entered, password):
            st.session_state.authed = True
            st.rerun()
        st.error("암호가 일치하지 않습니다.")
    st.stop()


def init_state():
    defaults = {
        "analysis": None, "snapshot": None, "error": "",
        "scope": ("all", None), "active_topic": "전체", "visible_articles": ARTICLE_PAGE,
        "enhanced": {}, "enhance_error": {}, "show_saved": False, "booted": False,
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


def load_snapshot() -> dict | None:
    """일일 스냅샷: 로컬 파일 → GitHub 비공개 저장소 순으로 시도."""
    if SNAPSHOT_PATH.exists():
        try:
            payload = json.loads(SNAPSHOT_PATH.read_text("utf-8"))
            if payload.get("analysis", {}).get("articles"):
                return payload
        except Exception:
            pass
    repo, token = secret("GITHUB_REPO"), secret("GITHUB_TOKEN")
    branch = secret("GITHUB_BRANCH") or "main"
    if not repo or not token:
        return None
    try:
        response = requests.get(
            f"https://api.github.com/repos/{repo}/contents/data/daily.json",
            params={"ref": branch},
            headers={"authorization": f"Bearer {token}", "accept": "application/vnd.github.raw+json",
                     "user-agent": "arthroscope-research-radar", "x-github-api-version": "2022-11-28"},
            timeout=30,
        )
        if response.ok:
            payload = response.json()
            if payload.get("analysis", {}).get("articles"):
                return payload
    except Exception:
        pass
    return None


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
    snap = st.session_state.snapshot
    return (snap or {}).get("suggestions", {}).get(idea_id)


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
    lines += ["", "## 연구 아이디어"]
    for n, idea in enumerate(ideas, 1):
        lines += [f"### {n}. {idea['title']}", "", idea["rationale"], "", f"- PICO: {idea['pico']}",
                  f"- 권장 설계: {idea['design']}", f"- 1차 결과: {idea['primaryEndpoint']}",
                  f"- 근거 PMID: {', '.join(e['pmid'] for e in idea['evidence'])}", ""]
    lines.append("> 주의: 아이디어는 선택된 저널과 기간에서 관찰된 신호를 기반으로 한 후보입니다. 최종 독창성 판단 전 전체 PubMed, 임상시험 등록자료 및 선행 프로토콜 검색이 필요합니다.")
    return "\n".join(lines)


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
    run_clicked = st.button("분석 시작 ↗", type="primary", use_container_width=True)
    creds = credentials()
    st.caption("NCBI E-utilities 기반 · 검색된 초록 전량 수집"
               + (" · API 키 적용" if creds.api_key else "")
               + (" · Gemini 고도화 가능" if secret("GEMINI_API_KEY") else " · GEMINI_API_KEY 없음"))
    if st.session_state.snapshot:
        st.info(f"일일 스냅샷 표시 중 ({fmt_dt(st.session_state.snapshot['generatedAt'])}). 조건을 바꿔 분석하면 실시간 결과로 전환됩니다.")

# 첫 진입: 스냅샷이 있으면 바로, 없으면 기본 1년 범위로 실시간 분석.
if not st.session_state.booted:
    st.session_state.booted = True
    snap = load_snapshot()
    if snap:
        st.session_state.snapshot = snap
        st.session_state.analysis = snap["analysis"]
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
    html = [f'<div class="ai"><div class="lbl">AI 동향 분석 · {scope_report.get("model", "")}</div>',
            f"<h3>{scope_report.get('headline', '')}</h3><p>{scope_report.get('summary', '')}</p>"]
    if scope_report.get("movements"):
        html.append("<ul>")
        for mv in scope_report["movements"]:
            links = " ".join(f'<a href="{pubmed(p)}" target="_blank">{p}</a>' for p in mv.get("evidence", []))
            html.append(f"<li><b>{mv['topic']}</b> {mv['reading']} <small>{links}</small></li>")
        html.append("</ul>")
    if scope_report.get("watchList"):
        html.append(f"<p><b>지켜볼 것</b> {' · '.join(scope_report['watchList'])}</p>")
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
        st.dataframe(tdf, hide_index=True, use_container_width=True,
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
# 03 논문 아이디어 후보
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
section("03", "논문 아이디어 후보", note)

b1, b2, b3 = st.columns([1, 1, 4])
if saved:
    if b1.button("← 분석 결과로" if show_saved else f"★ 저장한 아이디어 {len(saved)}", use_container_width=True):
        st.session_state.show_saved = not st.session_state.show_saved
        st.rerun()
if show_saved:
    b2.download_button("저장 목록 내려받기 ↓", saved_markdown(), f"arthroscope-saved-{date.today().isoformat()}.md",
                       "text/markdown", use_container_width=True)
else:
    b2.download_button("보고서 내려받기 ↓", report_markdown(analysis, active_trends, scope_label, scope_count, ideas, fell_back),
                       f"arthroscope-{analysis['dateTo']}.md", "text/markdown", use_container_width=True)

shown = saved if show_saved else ideas
gemini_key = secret("GEMINI_API_KEY")
gemini_model = secret("GEMINI_MODEL") or GEMINI_DEFAULT_MODEL

if not shown:
    st.info("이 범위에서 탐지된 연구 공백이 없습니다. 공백이 없으면 아이디어도 만들지 않습니다.")

for n, idea in enumerate(shown, 1):
    with st.container(border=True):
        top_l, top_r = st.columns([3, 1])
        top_l.markdown(f"**IDEA {n:02d}** &nbsp; " + "".join(f'<span class="tag">{t}</span>' for t in idea["tags"]), unsafe_allow_html=True)
        top_r.markdown(f"<div style='text-align:right;font-size:12px'>독창성 <b>{idea['novelty']}/5</b> · 실현성 <b>{idea['feasibility']}/5</b></div>", unsafe_allow_html=True)
        st.markdown(f"#### {idea['title']}")
        st.write(idea["rationale"])
        st.markdown(f"- **PICO** {idea['pico']}\n- **권장 설계** {idea['design']}\n- **1차 결과** {idea['primaryEndpoint']}")
        st.markdown("신호 근거 · " + " · ".join(f"[PMID {e['pmid']}]({pubmed(e['pmid'])})" for e in idea["evidence"]))

        c1, c2, _ = st.columns([1, 1, 3])
        saved_now = is_saved(idea["id"])
        if c1.button(("★ 저장됨" if saved_now else "☆ 저장"), key=f"save_{idea['id']}", use_container_width=True):
            toggle_saved(idea, scope_label)
            st.rerun()
        if not show_saved:
            label = "✦ 다시 고도화" if suggestion_for(idea["id"]) else "✦ AI로 고도화"
            if c2.button(label, key=f"enh_{idea['id']}", use_container_width=True, disabled=not gemini_key,
                         help=None if gemini_key else "GEMINI_API_KEY를 설정하면 사용할 수 있습니다."):
                pool = [a for a in analysis["articles"] if in_scope(a)]
                pmids = pmids_for_idea(idea, pool, analysis["trends"])
                if len(pmids) < ENHANCE_MIN:
                    st.session_state.enhance_error[idea["id"]] = f"근거 초록이 {len(pmids)}편뿐이라 고도화할 수 없습니다."
                else:
                    with st.spinner("근거 초록을 읽고 Gemini가 구체화하는 중…"):
                        try:
                            st.session_state.enhanced[idea["id"]] = enhance_idea(
                                idea, pmids, active_trends, scope_label, f"{analysis['dateFrom']}–{analysis['dateTo']}",
                                credentials(), gemini_key, gemini_model)
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
            if sug.get("droppedCitations"):
                meta += f" · 목록에 없던 인용 {sug['droppedCitations']}건 제거"
            if idea["id"] not in st.session_state.enhanced and snap:
                meta += f" · {fmt_dt(snap['generatedAt'])} 생성"
            with st.expander("AI 제안 보기", expanded=True):
                st.caption(meta)
                st.markdown(f"**연구 질문**  \n{sug.get('question', '')}")
                if sug.get("pico"):
                    p = sug["pico"]
                    st.markdown(f"**PICO**  \n- P: {p.get('population', '')}\n- I: {p.get('intervention', '')}\n- C: {p.get('comparison', '')}\n- O: {p.get('outcome', '')}")
                st.markdown(f"**선행연구 공백**  \n{sug.get('gap', '')}")
                st.markdown(f"**권장 설계**  \n{sug.get('design', '')}")
                if sug.get("limitations"):
                    st.markdown("**예상 한계**  \n" + "\n".join(f"- {x}" for x in sug["limitations"]))
                if sug.get("evidence"):
                    st.markdown("**근거 PMID**  \n" + "\n".join(f"- [PMID {e['pmid']}]({pubmed(e['pmid'])}) {e.get('note', '')}" for e in sug["evidence"]))

st.markdown(f'<div class="caveat"><b>중요 · ‘아이디어 후보’이지 독창성 확정 판정은 아닙니다.</b> 선택한 {len(analysis["journals"])}개 저널 밖의 PubMed 전체 검색, '
            "ClinicalTrials.gov/WHO ICTRP, PROSPERO 및 학회 초록을 확인한 뒤 연구계획서로 발전시키세요.</div>", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# 04 근거 초록
# ---------------------------------------------------------------------------

topic_options = ["전체"] + [t["label"] for t in active_trends[:5]]
if st.session_state.active_topic not in topic_options and st.session_state.active_topic != "전체":
    topic_options.append(st.session_state.active_topic)
filtered = [a for a in analysis["articles"] if in_scope(a)
            and (st.session_state.active_topic == "전체" or st.session_state.active_topic in a["topics"])]
section("04", "근거 초록", f"{len(filtered)}편 표시 가능")

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
        f'<h4><a href="{pubmed(a["pmid"])}" target="_blank">{a["title"]}</a></h4><p>{a["abstract"]}</p>'
        f'<div class="authors">{a["authors"]} &nbsp; ' + " ".join(f"#{t}" for t in a["topics"][:3]) + "</div></div>",
        unsafe_allow_html=True)
if st.session_state.visible_articles < len(filtered):
    if st.button(f"초록 더 보기 +{ARTICLE_PAGE}"):
        st.session_state.visible_articles += ARTICLE_PAGE
        st.rerun()

st.divider()
st.caption("ArthroScope · 근거를 읽고, 빈틈을 찾고, 다음 연구를 설계합니다. · Data: PubMed / NCBI E-utilities")
