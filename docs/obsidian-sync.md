# 모바일 저장 → Obsidian 동기화

배포 앱의 `☆ 저장`은 비공개 GitHub 저장소에 목록을 기록합니다. 노트북의
`ArthroScopeRadar-Obsidian` 예약 작업은 로그인 시와 매일 오전 9시에 목록을
읽어 Google Drive 안의 Obsidian 폴더에 개별 Markdown 노트를 만듭니다.
노트북이 꺼져 있으면 온라인 저장만 완료되고, 다음 예약 작업 실행 때 동기화됩니다.
Google Drive가 연결되어 있어야 하며 Obsidian 앱은 열지 않아도 됩니다.

## Streamlit Community Cloud 설정

앱의 Settings → Secrets에서 다음을 설정합니다. 토큰은 코드나 채팅에 넣지 않습니다.

```toml
SAVED_IDEAS_REPO = "cryptopatience/arthroscope-saved-ideas"
SAVED_IDEAS_TOKEN = "해당 비공개 저장소의 Contents 읽기·쓰기 권한이 있는 토큰"
```

기존 `GITHUB_TOKEN`이 해당 저장소에 읽기·쓰기 권한을 가지고 있다면
`SAVED_IDEAS_TOKEN`은 생략할 수 있습니다. 공개 저장소에는 저장하지 않습니다.
앱에서 **온라인 저장 완료** 메시지가 나와야 모바일 저장이 완료된 것입니다.
배포 환경에서는 노트북의 `OBSIDIAN_IDEAS_DIR` 경로를 설정하지 않습니다.

## 노트북 설정

로컬 `.streamlit/secrets.toml`에 `OBSIDIAN_IDEAS_DIR`을 설정합니다.
예약 작업은 Windows에 저장된 GitHub 인증으로 저장소를 읽습니다.

```powershell
python scripts/sync_saved_ideas.py
```

`scripts/sync_all_obsidian.py`는 기존 주간 동기화와 아이디어 동기화를 각각 실행합니다.
한 작업이 실패해도 다른 작업은 시도합니다. 결과 코드는 `data/obsidian_sync.log`에 남습니다.
`weekly=0 saved_ideas=0`은 두 작업 모두 성공했다는 뜻입니다.

같은 아이디어는 중복 노트를 만들지 않고 기존 노트와 사용자의 편집을 보존합니다.
저장 해제는 온라인 목록에서 제거하며 이미 만든 노트는 삭제하지 않습니다.
여러 기기의 목록 갱신은 클릭한 아이디어 단위로 합치고 충돌 시 최신 목록을 다시 읽습니다.
온라인 저장이 실패하면 저장 성공으로 표시하지 않습니다.
기존 로컬 아이디어는 앱의 별도 이전 버튼으로 온라인에 옮길 수 있습니다.
