#!/usr/bin/env python3
"""Copy chat.db into project backups/ with a UTC timestamp in the filename. Path from .env CHAT_DB_PATH via config."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_scripts = Path(__file__).resolve().parent
if str(_scripts) not in sys.path:
    sys.path.insert(0, str(_scripts))

import config  # noqa: E402
import macos_fda_paths  # noqa: E402


def _backup_cp(src: Path, dest: Path) -> tuple[bool, str]:
    """Copy via /bin/cp — TCC applies to cp, not Python (add /bin/cp to Full Disk Access)."""
    exe = macos_fda_paths.CP_SYSTEM
    if not Path(exe).is_file():
        return False, f"not found: {exe}"
    r = subprocess.run(
        [exe, "-p", str(src), str(dest)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip() or f"exit {r.returncode}"
        return False, err
    return True, ""


def _backup_sqlite3(src: Path, dest: Path) -> tuple[bool, str]:
    """Try SQLite .backup (opens DB in sqlite3 process — needs FDA on /usr/bin/sqlite3)."""
    exe = macos_fda_paths.SQLITE3_SYSTEM
    if not Path(exe).is_file():
        return False, f"not found: {exe}"
    qdest = shlex.quote(str(dest))
    r = subprocess.run(
        [exe, str(src), f".backup {qdest}"],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip() or f"exit {r.returncode}"
        return False, err
    return True, ""


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
    try:
        shutil.copy2(src, dest)
    except PermissionError:
        ok_cp, cp_err = _backup_cp(src, dest)
        if ok_cp and dest.is_file():
            print(f"Backed up {src} -> {dest} (via /bin/cp)")
            print(
                "Note: classification still uses Python to read chat.db — add Python.app + bin to FDA "
                "or the next daemon step will fail.",
                file=sys.stderr,
            )
            return 0
        ok_sql, sql_err = _backup_sqlite3(src, dest)
        if ok_sql and dest.is_file():
            print(f"Backed up {src} -> {dest} (via sqlite3 .backup)")
            print(
                "Note: classification still runs in Python — add Python paths to FDA "
                "or the next daemon step will fail when reading chat.db.",
                file=sys.stderr,
            )
            return 0
        hint = "\n".join(macos_fda_paths.fda_path_lines(_REPO_ROOT))
        exe = sys.executable
        real = os.path.realpath(exe)
        extra = ""
        if not ok_cp:
            extra += f"\n/bin/cp fallback failed: {cp_err}\n"
        if not ok_sql:
            extra += f"\nsqlite3 fallback failed: {sql_err}\n"
        print(
            f"Permission denied reading {src}.{extra}\n"
            "Easiest fix: add /bin/cp to Full Disk Access (one system path). "
            "You still need Python (Python.app + bin) for the agent to read chat.db.\n"
            f"Resolved interpreter: {real}\n"
            f"Entry point: {exe}\n\n"
            f"{hint}\n\n"
            "Remove stale entries, add the paths above, toggle each ON, quit System Settings, "
            "then log out/in (or restart) if it still fails.",
            file=sys.stderr,
        )
        return 1
    print(f"Backed up {src} -> {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
