# ArthroScope Research Radar — Streamlit

Next.js/Cloudflare 버전을 Python + Streamlit으로 옮긴 것입니다. 기능은 동일합니다.

- 10개 정형외과 저널의 PubMed 초록 수집 (NCBI E-utilities)
- 무릎 논문만 추려 주제 분류 → 반기 비교 트렌드 → 연구 공백 기반 아이디어 생성
- 탐지된 공백을 **추천 연구기회**와 **구조적 공백**으로 나눠 표시 (아래 참고)
- 계열·저널별 범위 전환, 아이디어 저장, 보고서(.md) 다운로드
- Gemini로 아이디어 고도화 (선택)
- GitHub Actions 일일 스냅샷 (선택)

## 공백을 나누는 기준

통계가 찾아낸 공백이 전부 연구 기회인 것은 아닙니다. 인공관절 감염 연구에 PROM이
적은 것은 연구가 부족해서가 아니라 그 분야의 1차 결과가 균 박멸과 임플란트 생존이기
때문입니다. 그래서 판정 단계를 거쳐 화면을 둘로 나눕니다.

- **추천 연구기회** — 임상적으로 답이 필요한데 실제로 비어 있다고 판정된 공백
- **구조적 공백** — 통계적으로만 비어 있는 공백. 지우지 않고 **왜 추천하지 않았는지**와
  **그 분야가 실제로 쓰는 1차 결과**를 함께 보여줍니다. 같은 주제로 연구를 설계할 때
  무엇을 결과변수로 잡아야 하는지가 여기서 나옵니다.

각 판정에는 모델이 스스로 매긴 확신도 대신, 밖에서 관측한 네 가지를 붙입니다.

| 항목 | 어떻게 재는가 |
|---|---|
| 판정 안정성 | 같은 프롬프트를 주 모델로 5회 돌려 같은 판정이 나온 비율 |
| 모델 간 합의 | 두 번째 Gemini 모델(및 `OPENAI_API_KEY`가 있으면 OpenAI)의 판정과 일치하는지 |
| 시간적 근거 | 기간을 전반기·후반기로 나눠 다시 쟀을 때 공백이 좁혀졌는지·유지됐는지 |
| 표본 충분성 | 그 공백을 잰 문헌 수와 효과크기(Cohen's h) |

넷을 합쳐 **종합 근거 수준**(높음·중간·낮음)을 냅니다. 판정과 방향이 맞는 신호는
더하고 반박하는 신호는 뺍니다 — 예를 들어 구조적 공백이라면 공백이 그대로 유지돼야
판정을 지지하고, 연구기회라면 최근 반기에 실제로 좁혀지고 있어야 지지합니다.
아직 재지 않은 항목이 하나도 없으면 `미측정`으로 표시하고 등급을 매기지 않습니다.

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

앱은 `data/daily.json`이 로컬에 있으면 그것을 먼저 보여주고, 없으면 GitHub에서 시도하고, 그것도 없으면 기본 1년 범위로 실시간 분석을 돌립니다. 사이드바의 운영 로그(`data/run_log.json`)도 같은 순서로 읽습니다.

## 일일 스냅샷

```bash
python scripts/daily.py   # data/daily.json + data/run_log.json 생성
```

`data/run_log.json`에는 실행마다 한 줄씩(수집 편수·공백 수·AI 갱신 여부·소요 시간, 실패했다면 그 사유) 쌓입니다. 앱 사이드바의 **운영 로그**가 이 파일을 그대로 읽어 마지막 수집 시각, 최근 7일 중 며칠이나 돌았는지, 실패가 있었는지를 보여줍니다. 스냅샷만으로는 "마지막 결과"밖에 알 수 없어 며칠째 멈춘 것을 눈치채기 어렵습니다.

`.github/workflows/daily.yml`이 매일 18:00 KST에 같은 스크립트를 돌려 커밋합니다. 저장소 Secrets에 `NCBI_API_KEY`, `NCBI_TOOL_EMAIL`, `GEMINI_API_KEY`를, Variables에 `GEMINI_MODEL`을 넣으세요.

공백 판정은 이 일일 작업에서만 돕니다(앱은 스냅샷의 판정을 읽기만 합니다). 그래서 판정단 설정도 앱의 `secrets.toml`이 아니라 이 작업의 환경변수로 줍니다.

| 환경변수 | 용도 |
|---|---|
| `GEMINI_JUDGE_MODEL_B` | 교차 검증용 두 번째 Gemini 모델. 비우면 `gemini-2.5-flash` |
| `OPENAI_API_KEY`, `OPENAI_JUDGE_MODEL` | 넣으면 다른 회사 모델까지 판정단에 들어갑니다 (기본 `gpt-4o`). 없으면 Gemini 두 모델로만 합의를 잽니다 |
| `GEMINI_FORCE=1` | 요일과 무관하게 Gemini 단계를 강제 실행. 판정 로직을 바꾼 직후에 씁니다 |

## Streamlit Community Cloud 배포

저장소를 연결하고 `app.py`를 메인 파일로 지정한 뒤, 앱 설정의 Secrets 란에 `secrets.toml` 내용을 붙여 넣으면 됩니다.

## 구조

```
app.py                 # 화면 (app/page.tsx)
radar/analysis.py      # 수집·분류·트렌드·아이디어 + 공백의 크기·시간 변화 지표
radar/ncbi.py          # E-utilities 호출·재시도 (lib/ncbi.ts)
radar/gemini.py        # Gemini 프롬프트·호출·고도화 (lib/gemini.ts, api/enhance)
radar/judge.py         # 공백 판정(반복 실행·모델 간 합의)과 종합 근거 수준
scripts/daily.py       # 일일 스냅샷 + 실행 기록 (scripts/daily.ts)
```

판정 한 건에 주 모델 5회 + 나머지 판정자 1회씩을 씁니다. 제목만 보내는 짧은 프롬프트고
Gemini 단계 자체가 주 1회(금요일)만 돌기 때문에, 반복 비용보다 "확신도 4점 일색"을
없앤 값이 큽니다. `radar/judge.py`의 `JUDGE_RUNS`로 조절합니다.

원본과 다른 점: 저장한 아이디어는 브라우저 localStorage 대신 `data/saved_ideas.json`에 보관됩니다.
