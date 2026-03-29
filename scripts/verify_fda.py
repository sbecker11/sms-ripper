#!/usr/bin/env python3
"""
Verify Full Disk Access by probing real access to chat.db (macOS does not expose a public TCC read API).

Checks:
  - This Python process can open the DB (needed for main.py / reader).
  - /bin/cp can copy it (backup fallback).
  - /usr/bin/sqlite3 can read it (backup fallback).

Run with the same interpreter the daemon uses:
  ./venv/bin/python scripts/verify_fda.py
  poe verify-fda

Exit 0 if all checks pass, 1 otherwise.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import config  # noqa: E402


def _check_python(db: Path) -> tuple[str, bool, str]:
    label = f"Python open() — {sys.executable}"
    try:
        with open(db, "rb") as f:
            f.read(1)
        return label, True, "ok"
    except PermissionError:
        real = os.path.realpath(sys.executable)
        return (
            label,
            False,
            f"Permission denied — add to Full Disk Access: {real} (and Python.app if Homebrew)",
        )
    except OSError as e:
        return label, False, str(e)


def _check_cp(db: Path) -> tuple[str, bool, str]:
    label = "/bin/cp -p (backup fallback)"
    if not Path("/bin/cp").is_file():
        return label, False, "/bin/cp missing"
    with tempfile.TemporaryDirectory(prefix="sms-ripper-fda-") as td:
        dest = Path(td) / "probe.bak"
        r = subprocess.run(
            ["/bin/cp", "-p", str(db), str(dest)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if r.returncode == 0 and dest.is_file():
            return label, True, "ok"
        err = (r.stderr or r.stdout or "").strip() or f"exit {r.returncode}"
        return label, False, f"{err} — add /bin/cp to Full Disk Access"


def _check_sqlite3(db: Path) -> tuple[str, bool, str]:
    label = "/usr/bin/sqlite3 (backup fallback)"
    exe = "/usr/bin/sqlite3"
    if not Path(exe).is_file():
        return label, False, f"{exe} missing"
    r = subprocess.run(
        [exe, str(db), "SELECT 1;"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if r.returncode == 0:
        return label, True, "ok"
    err = (r.stderr or r.stdout or "").strip() or f"exit {r.returncode}"
    return label, False, f"{err} — add {exe} to Full Disk Access"


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Full Disk Access via chat.db reads")
    parser.add_argument(
        "--chat-db",
        type=Path,
        default=None,
        help="Override DB path (default: CHAT_DB_PATH from .env)",
    )
    args = parser.parse_args()
    db = (args.chat_db or Path(config.CHAT_DB_PATH)).expanduser()

    print(f"Probing access to: {db}\n")
    print(
        "(This does not read Apple’s TCC database — it only tries the same operations sms-ripper uses.)\n"
    )

    if not db.is_file():
        print(f"FAIL: not a file: {db}")
        return 1

    rows: list[tuple[str, bool, str]] = [
        _check_python(db),
        _check_cp(db),
        _check_sqlite3(db),
    ]

    w = max(len(r[0]) for r in rows)
    all_ok = True
    for label, ok, detail in rows:
        status = "PASS" if ok else "FAIL"
        print(f"{status:4}  {label:{w}s}  {detail}")
        if not ok:
            all_ok = False

    print()
    if all_ok:
        print("All probes passed — FDA (or equivalent access) looks sufficient for backup + Python reads.")
        print("If the launchd job still fails, compare: plist uses venv/bin/python — run this script with that same binary.")
        return 0

    print("Some probes failed — add the listed paths in System Settings → Privacy & Security → Full Disk Access.")
    print("Guided: poe fda-assist   List: poe daemon-fda-path")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
