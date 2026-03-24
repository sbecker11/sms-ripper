#!/usr/bin/env python3
"""Print a copy-pasteable shell command to cd to this repo root (backups, logs, blocklist)."""

from __future__ import annotations

import shlex
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    print(f"cd {shlex.quote(str(_ROOT))}")


if __name__ == "__main__":
    main()
