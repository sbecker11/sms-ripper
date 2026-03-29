#!/usr/bin/env python3
"""Print paths to add for macOS Full Disk Access (framework Python.app + bin + sqlite3)."""

from __future__ import annotations

import sys
from pathlib import Path

import macos_fda_paths

_REPO = Path(__file__).resolve().parent.parent


def main() -> int:
    vpy = _REPO / "venv" / "bin" / "python"
    if not vpy.is_file():
        print("No venv at ./venv/bin/python — create the venv first.", file=sys.stderr)
        return 1
    for line in macos_fda_paths.fda_path_lines(_REPO):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
