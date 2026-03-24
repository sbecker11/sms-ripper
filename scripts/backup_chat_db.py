#!/usr/bin/env python3
"""Copy chat.db into project backups/ with a UTC timestamp in the filename. Path from .env CHAT_DB_PATH via config."""

from __future__ import annotations

import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import config  # noqa: E402


def main() -> int:
    src = Path(config.CHAT_DB_PATH).expanduser()
    if not src.is_file():
        print(f"Not found or not a file: {src}", file=sys.stderr)
        return 1
    root = Path(__file__).resolve().parent.parent
    dest_dir = root / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    # e.g. chat.db.UTC-2026-03-24T22-02-41Z.bak (colons → - for safe filenames)
    ts = now.strftime("%Y-%m-%dT%H-%M-%S") + "Z"
    dest = dest_dir / f"chat.db.UTC-{ts}.bak"
    shutil.copy2(src, dest)
    print(f"Backed up {src} -> {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
