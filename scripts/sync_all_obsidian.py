"""Run both existing weekly archive sync and private saved-idea sync."""
import argparse
from datetime import datetime
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--weekly-script", required=True)
    parser.add_argument("--weekly-destination", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    results = []
    commands = [
        [sys.executable, args.weekly_script, "--destination", args.weekly_destination],
        [sys.executable, str(root / "scripts/sync_saved_ideas.py")],
    ]
    for command in commands:
        try:
            completed = subprocess.run(command, cwd=root, capture_output=True, timeout=300,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            results.append(completed.returncode)
        except (OSError, subprocess.TimeoutExpired):
            results.append(1)
    log = root / "data/obsidian_sync.log"
    with log.open("a", encoding="utf-8") as handle:
        handle.write(f"{datetime.now().astimezone().isoformat()} weekly={results[0]} saved_ideas={results[1]}\n")
    return int(any(results))


if __name__ == "__main__":
    raise SystemExit(main())
