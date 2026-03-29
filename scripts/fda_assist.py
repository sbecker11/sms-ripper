#!/usr/bin/env python3
"""
Painless Full Disk Access setup for sms-ripper:

1) Opens System Settings to Full Disk Access (best-effort deep link).
2) Interactive (terminal): copies each path to the clipboard one-by-one; you paste with Cmd+Shift+G in the + file picker.
3) Non-interactive: writes paths to a temp file and opens it in TextEdit.

Usage: python scripts/fda_assist.py
       poe fda-assist
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

_scripts = Path(__file__).resolve().parent
if str(_scripts) not in sys.path:
    sys.path.insert(0, str(_scripts))

import macos_fda_paths  # noqa: E402

_REPO = Path(__file__).resolve().parent.parent

# Opens Security → Full Disk Access on many macOS versions (Ventura+ may land on Privacy & Security first).
_FDA_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"


def _open_fda_pane() -> None:
    subprocess.run(
        ["open", _FDA_URL],
        check=False,
    )


def _copy_clipboard(text: str) -> None:
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)


def _write_reference_file(paths: list[str]) -> Path:
    import os

    fd, name = tempfile.mkstemp(prefix="sms-ripper-fda-paths-", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(
            "Paste each line into Full Disk Access → + → Cmd+Shift+G (Go to Folder), then enable the toggle.\n\n"
        )
        for p in paths:
            f.write(p + "\n")
    return Path(name)


def main() -> int:
    paths = macos_fda_paths.fda_absolute_paths(_REPO)
    if not paths:
        print("No venv at ./venv/bin/python — create the venv first.", file=sys.stderr)
        return 1

    _open_fda_pane()

    if sys.stdin.isatty():
        print(
            "\nSystem Settings (Full Disk Access) should be opening.\n"
            "\n"
            "For EACH path, do this in that window:\n"
            "  1. Click the + button\n"
            "  2. Press Cmd+Shift+G (Go to Folder)\n"
            "  3. Paste with Cmd+V and press Return / click Open\n"
            "  4. Turn the new row’s switch ON\n"
            "\n"
            "This script copies ONE path at a time to your clipboard.\n"
            "After you finish that path in Settings, return to THIS terminal and press Enter "
            "so the next path is copied. Enter does not “confirm” anything to macOS — it only advances the script.\n"
        )
        for i, p in enumerate(paths, start=1):
            _copy_clipboard(p)
            print(f"\n--- Path {i} of {len(paths)} (now on clipboard) ---\n{p}\n")
            if i < len(paths):
                input("Done with that one in System Settings? Press Enter here for the next clipboard path… ")
            else:
                print("(Last path — add it in Settings; no Enter needed after this.)\n")
        print("All paths shown. If Settings did not open: System Settings → Privacy & Security → Full Disk Access.\n")
        return 0

    ref = _write_reference_file(paths)
    subprocess.run(["open", "-e", str(ref)], check=False)
    subprocess.run(
        [
            "/usr/bin/osascript",
            "-e",
            'display alert "SMS Ripper: Full Disk Access" message '
            '"TextEdit has one path per line. For each: click +, Cmd+Shift+G, paste, Open, enable the toggle. '
            'Path list also printed in stdout."',
        ],
        check=False,
    )
    print(ref, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
