"""Sync private online saves into the configured local Obsidian folder."""
from pathlib import Path
import subprocess
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from radar.cloud_saved import CloudStore
from radar.obsidian import export_idea


def main():
    os.chdir(Path(__file__).resolve().parents[1])
    # Windows Credential Manager authentication; never print the credential.
    result = subprocess.run(
        ["git", "-c", "credential.interactive=never", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n", text=True, capture_output=True,
        timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"})
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    token = fields.get("password", "") if result.returncode == 0 else ""
    values, _ = CloudStore(token=token).read()
    for idea in values:
        if export_idea(idea) is None:
            raise RuntimeError("OBSIDIAN_IDEAS_DIR is required")
    print(f"Saved ideas synced: {len(values)}")


if __name__ == "__main__":
    main()
