"""AI 결과 캐시. 배치와 앱이 같은 열쇠 계산을 쓰도록 한곳에 모았다.

야간 배치는 어제 스냅샷 안에 담긴 결과를 열쇠로 검증해 재사용한다(reuse).
앱에는 그 스냅샷이 없을 때가 많다 — 사이드바에서 **분석 시작**을 누르면
스냅샷을 통째로 버리므로, 화면의 모든 카드가 저장된 제안 없이 남는다. 그래서
앱 쪽은 파일 하나에 따로 쌓는다. 같은 아이디어에 같은 근거 초록·같은 모델이면
답도 같으니, 새로고침이나 세션 종료 뒤에도 Gemini를 다시 부르지 않는다.

배치의 스냅샷과 앱의 이 파일은 서로 다른 저장소다. 열쇠에 범위 문구가 들어가는데
배치는 "무릎 전체", 앱은 "선택한 저널 전체"로 서로 다르게 부르고, 그 문구가 실제로
프롬프트에 들어가 답을 바꾼다. 억지로 같은 칸을 보게 만들면 안 된다.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

CACHE_PATH = Path("data/enhance_cache.json")   # 커밋하지 않는다(.gitignore)
# 항목 하나가 제안 하나(길면 10KB). 150개면 파일이 1.5MB쯤에서 멈춘다. 넘치면 오래
# 들어온 것부터 버린다 — 근거 초록이 매일 바뀌어 옛 열쇠는 어차피 다시 맞지 않는다.
MAX_ENTRIES = 150
_memo: dict[str, tuple[tuple, dict]] = {}   # 앱은 위젯을 건드릴 때마다 스크립트를 통째로 다시 돈다


def cache_key(kind: str, ident: str, scope: str, pmids: list[str], model: str) -> str:
    """같은 아이디어에 같은 근거 초록·같은 모델이면 답도 같다. 그러면 다시 부르지 않는다."""
    raw = "|".join([kind, ident, scope, model, *sorted(pmids)])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def reuse(cached: dict | None, key: str) -> dict | None:
    if isinstance(cached, dict) and cached.get("cacheKey") == key and not cached.get("error"):
        return cached
    return None


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _stamp(path: Path) -> tuple:
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return ()


def load(path: Path = CACHE_PATH) -> dict:
    """파일이 그대로면 다시 읽지 않는다 — 리런마다 1MB짜리 JSON을 파싱하지 않도록."""
    stamp = _stamp(path)
    cached = _memo.get(str(path))
    if cached and cached[0] == stamp:
        return cached[1]
    data = _read(path)
    _memo[str(path)] = (stamp, data)
    return data


def get(key: str, path: Path = CACHE_PATH) -> dict | None:
    return reuse(load(path).get(key), key)


def put(key: str, value: dict, path: Path = CACHE_PATH) -> None:
    """실패한 결과는 담지 않는다 — 다음에 다시 시도할 수 있어야 한다."""
    if not isinstance(value, dict) or value.get("error"):
        return
    try:
        data = _read(path)           # 쓰기 직전에는 기억해 둔 사본이 아니라 디스크를 본다
        data.pop(key, None)          # 다시 넣어 맨 뒤로: 최근에 쓴 것이 늦게 버려진다
        data[key] = {**value, "cacheKey": key}
        for stale in list(data)[:max(0, len(data) - MAX_ENTRIES)]:
            del data[stale]
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
        os.replace(tmp, path)        # 두 세션이 동시에 눌러도 반쪽짜리 파일이 남지 않는다
        _memo[str(path)] = (_stamp(path), data)
    except Exception:
        pass  # 캐시는 편의 기능. 못 쓰면 그냥 다시 부른다.
