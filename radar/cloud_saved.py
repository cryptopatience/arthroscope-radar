"""Shared saved-idea list in a private GitHub repository, with conflict retries."""
from __future__ import annotations

import base64
import json
import requests

from radar.secrets import secret

DEFAULT_REPO = "cryptopatience/arthroscope-saved-ideas"


class CloudSaveError(RuntimeError):
    pass


def configured() -> bool:
    return bool(secret("SAVED_IDEAS_TOKEN") or secret("GITHUB_TOKEN"))


class CloudStore:
    def __init__(self, token=None, repo=None):
        token = token or secret("SAVED_IDEAS_TOKEN") or secret("GITHUB_TOKEN")
        if not token:
            raise CloudSaveError("온라인 저장용 GitHub 토큰이 설정되지 않았습니다.")
        self.root = "https://api.github.com/repos/" + (repo or secret("SAVED_IDEAS_REPO") or DEFAULT_REPO)
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}",
                                     "Accept": "application/vnd.github+json"})

    def request(self, method, suffix, **kwargs):
        try:
            response = self.session.request(method, self.root + suffix, timeout=30,
                                            allow_redirects=False, **kwargs)
        except requests.RequestException:
            raise CloudSaveError("온라인 저장소에 연결할 수 없습니다. 다시 시도해 주세요.") from None
        return response

    def read(self):
        # Never send saved research ideas to a public repository.
        info = self.request("GET", "")
        if info.status_code != 200 or not info.json().get("private"):
            raise CloudSaveError("비공개 아이디어 저장소와 토큰의 접근 권한을 확인해 주세요.")
        response = self.request("GET", "/contents/saved_ideas.json")
        if response.status_code == 404:
            return [], None
        if response.status_code != 200:
            raise CloudSaveError(f"온라인 목록을 읽지 못했습니다 (HTTP {response.status_code}).")
        try:
            item = response.json()
            values = json.loads(base64.b64decode(item["content"]))
            if not isinstance(values, list) or any(not isinstance(i, dict) or not all(k in i for k in ("id", "title", "savedAt")) for i in values):
                raise ValueError()
            return values, item["sha"]
        except (KeyError, ValueError, TypeError):
            raise CloudSaveError("온라인 저장 목록 형식이 올바르지 않습니다.") from None

    def update(self, idea, remove=False):
        for attempt in range(3):
            values, sha = self.read()
            # Merge only the clicked item, not the potentially stale session list.
            previous = next((i for i in values if i["id"] == idea["id"]), None)
            values = [i for i in values if i["id"] != idea["id"]]
            if not remove:
                clean = {k: v for k, v in (previous or idea).items() if k != "obsidianPath"}
                values.insert(0, clean)
            payload = {"message": "Update saved research ideas",
                       "content": base64.b64encode(json.dumps(values, ensure_ascii=False, indent=2).encode()).decode()}
            if sha:
                payload["sha"] = sha
            response = self.request("PUT", "/contents/saved_ideas.json", json=payload)
            if response.status_code in (200, 201):
                return values
            if response.status_code not in (409, 422):
                break
        raise CloudSaveError(f"온라인 저장에 실패했습니다 (HTTP {response.status_code}). 토큰의 Contents 쓰기 권한을 확인하고 다시 시도해 주세요.")
