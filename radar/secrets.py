"""설정값 읽기 — 환경변수 우선, 없으면 .streamlit/secrets.toml.

앱(app.py)은 st.secrets를 읽고 야간 작업은 환경변수를 읽는데, 스크립트를 손으로
돌릴 때만 둘 다 아니어서 "키가 없다"가 됐다. 로컬에는 secrets.toml에 키가 다
들어 있는데도 그렇다. 그래서 스크립트는 여기를 거친다.

환경변수를 먼저 본다 — GitHub Actions는 저장소 Secrets를 환경변수로 주입하고,
그쪽이 항상 이겨야 한다. secrets.toml은 로컬 실행의 편의일 뿐이다.
"""
from __future__ import annotations

import os
from pathlib import Path

SECRETS_PATH = Path(".streamlit/secrets.toml")
_cache: dict[str, str] | None = None


def _file_secrets() -> dict[str, str]:
    """secrets.toml을 한 번만 읽어 둔다. 없거나 깨졌으면 빈 사전."""
    global _cache
    if _cache is not None:
        return _cache
    _cache = {}
    try:
        import tomllib   # 3.11+. 없으면 조용히 환경변수만 쓴다.
        data = tomllib.loads(SECRETS_PATH.read_text("utf-8"))
        _cache = {k: str(v) for k, v in data.items() if isinstance(v, (str, int, float, bool))}
    except Exception:
        pass
    return _cache


def secret(name: str, default: str = "") -> str:
    value = os.environ.get(name, "").strip()
    if value:
        return value
    return _file_secrets().get(name, default).strip()


def source(name: str) -> str:
    """이 값이 어디서 왔는지. 실행 로그에 찍어 두면 "키가 없다"를 바로 짚을 수 있다."""
    if os.environ.get(name, "").strip():
        return "환경변수"
    if _file_secrets().get(name, "").strip():
        return "secrets.toml"
    return "없음"
