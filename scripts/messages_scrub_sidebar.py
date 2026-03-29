#!/usr/bin/env python3
"""
Automate the "double-click list row" workaround for ghost unread rows in Messages
(No Conversation Selected → second click makes the row disappear).

Requires:
  - System Settings → Privacy & Security → Accessibility: enable your terminal app
    (and Python if you run via `python` directly).
  - Messages visible (not hidden); main window open. Works best with the conversation
    list focused (click the sidebar once before running, or rely on activate).

Fragile: Apple changes the UI hierarchy between macOS versions. This script tries several
common AX paths. If it fails, use the on-screen Accessibility Inspector to adjust paths.

Examples:
  python scripts/messages_scrub_sidebar.py --rows 25
  python scripts/messages_scrub_sidebar.py --rows 40 --delay 0.35 --dry-run
"""

from __future__ import annotations

import argparse
import subprocess
import sys


def _build_applescript(max_rows: int, delay_sec: float) -> str:
    # delay_sec as string for AppleScript literal (e.g. 0.3)
    d = f"{delay_sec:.3f}".rstrip("0").rstrip(".")
    if not d:
        d = "0"
    mr = int(max_rows)
    return f'''
-- sms-ripper: scrub ghost rows via double-click on conversation list (best-effort)
-- UI terms (outline, row, etc.) must appear inside "tell process Messages"; not in a top-level handler.

tell application "Messages" to activate
delay 0.6

tell application "System Events"
  tell process "Messages"
    set frontmost to true
    delay 0.4
    set w to window 1
    set o to missing value
    try
      set o to outline 1 of scroll area 1 of splitter group 1 of w
    end try
    if o is missing value then
      try
        set o to outline 1 of scroll area 1 of splitter group 2 of w
      end try
    end if
    if o is missing value then
      try
        set o to outline 1 of scroll area 1 of w
      end try
    end if
    if o is missing value then
      try
        set o to outline 1 of scroll area 2 of splitter group 1 of w
      end try
    end if
    if o is missing value then
      error "Could not find conversation outline (UI changed?). Click the sidebar once and retry, or update findOutline paths in messages_scrub_sidebar.py."
    end if
    set rowList to every row of o
    set rc to count of rowList
    set lim to {mr}
    if rc < lim then set lim to rc
    if lim < 1 then error "No rows in sidebar outline."
    repeat with i from 1 to lim
      try
        set r to item i of rowList
        set p to position of r
        set s to size of r
        set cx to (item 1 of p) + ((item 1 of s) div 2)
        set cy to (item 2 of p) + ((item 2 of s) div 2)
        set clickLoc to {{cx, cy}}
        click at clickLoc
        delay {d}
        click at clickLoc
        delay {d}
      end try
    end repeat
  end tell
end tell

return "OK"
'''


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Double-click each row in Messages sidebar (ghost unread workaround)."
    )
    parser.add_argument(
        "--rows",
        type=int,
        default=30,
        metavar="N",
        help="Max sidebar rows to scrub from the top (default: 30).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.28,
        metavar="SEC",
        help="Pause between clicks and between rows (default: 0.28).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the AppleScript only; do not run Messages automation.",
    )
    args = parser.parse_args()
    if args.rows < 1:
        print("--rows must be >= 1", file=sys.stderr)
        return 2
    if args.delay < 0.05:
        print("--delay must be >= 0.05", file=sys.stderr)
        return 2

    script = _build_applescript(args.rows, args.delay)
    if args.dry_run:
        print(script)
        return 0

    # Multiline scripts are more reliable via stdin than a single -e argument.
    r = subprocess.run(
        ["osascript", "-"],
        input=script,
        capture_output=True,
        text=True,
        timeout=max(120, args.rows * int(args.delay * 4) + 60),
    )
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        print(err or "osascript failed", file=sys.stderr)
        return 1
    out = (r.stdout or "").strip()
    if out:
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
