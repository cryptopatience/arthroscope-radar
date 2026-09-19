"""Export saved ideas as individual Obsidian Markdown notes."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from radar.secrets import secret


def notes_directory() -> str:
    return secret("OBSIDIAN_IDEAS_DIR")


def note_markdown(idea: dict) -> str:
    title = str(idea["title"]).replace("\n", " ")
    saved_at = str(idea["savedAt"])
    lines = ["---", "type: research-idea",
             "title: " + json.dumps(title, ensure_ascii=False),
             "saved_at: " + json.dumps(saved_at),
             "idea_id: " + json.dumps(str(idea["id"]), ensure_ascii=False),
             "source: ArthroScope Research Radar", "---", "",
             f"# {title}", "", f"- 저장 일시: {saved_at.replace('T', ' ')}",
             f"- 저장 범위: {idea.get('scope', '')}",
             f"- 주제: {', '.join(idea.get('tags', []))}",
             f"- 독창성: {idea.get('novelty', '')}/5 · 실현성: {idea.get('feasibility', '')}/5",
             "", "## 연구 근거", "", str(idea.get("rationale", "")),
             "", "## 연구 계획", "", f"- PICO: {idea.get('pico', '')}",
             f"- 권장 설계: {idea.get('design', '')}",
             f"- 1차 결과변수: {idea.get('primaryEndpoint', '')}",
             "", "## 참고 논문", ""]
    for evidence in idea.get("evidence", []):
        pmid = evidence.get("pmid", "")
        lines.append(f"- [PMID {pmid}](https://pubmed.ncbi.nlm.nih.gov/{pmid}/) — {evidence.get('title', '')}")
    lines += ["", "## 원본 데이터", "", "```json",
              json.dumps(idea, ensure_ascii=False, indent=2), "```", ""]
    return "\n".join(lines)


def export_idea(idea: dict, directory: str | Path | None = None) -> Path | None:
    target = directory if directory is not None else notes_directory()
    if not target:
        return None
    folder = Path(target)
    # Require the configured folder to exist so a disconnected drive is reported.
    if not folder.is_dir():
        raise OSError(f"Obsidian 저장 폴더에 접근할 수 없습니다: {folder}")
    key = hashlib.sha256(str(idea["id"]).encode("utf-8")).hexdigest()[:12]
    existing = next(folder.glob(f"*--{key}.md"), None)
    if existing is not None:
        return existing  # Preserve any edits the user made in Obsidian.
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f\[\]#^]', " ", str(idea["title"]))
    title = re.sub(r"\s+", " ", title).strip(" .")[:85].rstrip(" .") or "연구 아이디어"
    saved_date = str(idea["savedAt"])[:10]
    path = folder / f"{saved_date} - {title}--{key}.md"
    content = note_markdown(idea)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    except FileExistsError:
        pass
    return path
