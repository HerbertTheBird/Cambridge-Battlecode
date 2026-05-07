#!/usr/bin/env python3
"""Run the tournament publisher immediately, then roughly every 10 minutes."""

from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
PUBLISHER = ROOT_DIR / "tools" / "publish_tournament_site.sh"
INTERVAL_SECONDS = int(os.environ.get("TOURNAMENT_WATCH_INTERVAL_SECONDS", "600"))
LOG_FILE = Path(os.environ.get("TOURNAMENT_WATCH_LOG", ROOT_DIR / "public_tournament_site" / "publisher.log"))


def timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def log(message: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp()}] {message}\n")


def run_once() -> None:
    log("starting tournament publish run")
    result = subprocess.run(
        [str(PUBLISHER)],
        cwd=ROOT_DIR,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.stdout:
        log("stdout:\n" + result.stdout.rstrip())
    if result.stderr:
        log("stderr:\n" + result.stderr.rstrip())
    if result.returncode == 0:
        log("finished tournament publish run")
    else:
        log(f"tournament publish failed with status {result.returncode}")


def main() -> None:
    while True:
        run_once()
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
