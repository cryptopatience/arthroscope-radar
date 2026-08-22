# ArthroScope Research Radar — Streamlit

Next.js/Cloudflare 버전을 Python + Streamlit으로 옮긴 것입니다. 기능은 동일합니다.

- 10개 정형외과 저널의 PubMed 초록 수집 (NCBI E-utilities)
- 무릎 논문만 추려 주제 분류 → 반기 비교 트렌드 → 연구 공백 기반 아이디어 생성
- 계열·저널별 범위 전환, 아이디어 저장, 보고서(.md) 다운로드
- Gemini로 아이디어 고도화 (선택)
- GitHub Actions 일일 스냅샷 (선택)

## 실행

```bash
pip install -r requirements.txt
streamlit run app.py
```

## 설정 (선택)

`.streamlit/secrets.toml.example`을 `.streamlit/secrets.toml`로 복사해 채우거나, 같은 이름의 환경변수를 쓰면 됩니다.

| 키 | 용도 |
|---|---|
| `APP_PASSWORD` | 접근 암호. 설정하면 첫 화면에서 암호를 묻습니다. 비우면 잠기지 않습니다 |
| `NCBI_API_KEY`, `NCBI_TOOL_EMAIL` | 초당 요청 한도 3 → 10회, 초록 수집 병렬화 |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | "AI로 고도화" 버튼 활성화 |
| `GITHUB_REPO`, `GITHUB_TOKEN`, `GITHUB_BRANCH` | 비공개 저장소의 `data/daily.json` 스냅샷 읽기 |

앱은 `data/daily.json`이 로컬에 있으면 그것을 먼저 보여주고, 없으면 GitHub에서 시도하고, 그것도 없으면 기본 1년 범위로 실시간 분석을 돌립니다.

## 일일 스냅샷

```bash
python scripts/daily.py   # data/daily.json 생성
```

`.github/workflows/daily.yml`이 매일 18:00 KST에 같은 스크립트를 돌려 커밋합니다. 저장소 Secrets에 `NCBI_API_KEY`, `NCBI_TOOL_EMAIL`, `GEMINI_API_KEY`를, Variables에 `GEMINI_MODEL`을 넣으세요.

## Streamlit Community Cloud 배포

저장소를 연결하고 `app.py`를 메인 파일로 지정한 뒤, 앱 설정의 Secrets 란에 `secrets.toml` 내용을 붙여 넣으면 됩니다.

## 구조

```
app.py                 # 화면 (app/page.tsx)
radar/analysis.py      # 수집·분류·트렌드·아이디어 (lib/analysis.ts)
radar/ncbi.py          # E-utilities 호출·재시도 (lib/ncbi.ts)
radar/gemini.py        # Gemini 프롬프트·호출·고도화 (lib/gemini.ts, api/enhance)
scripts/daily.py       # 일일 스냅샷 (scripts/daily.ts)
```

원본과 다른 점: 저장한 아이디어는 브라우저 localStorage 대신 `data/saved_ideas.json`에 보관됩니다.
