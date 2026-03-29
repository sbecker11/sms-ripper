#!/usr/bin/env python3
"""Show a blocking macOS alert (osascript). Used by daemon_cycle on failures."""

from __future__ import annotations

import subprocess
import sys


def _applescript_string_literal(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def main() -> int:
    msg = " ".join(sys.argv[1:]).strip() if len(sys.argv) > 1 else "Unknown error."
    msg = msg.replace("\r\n", "\n").replace("\r", "\n")
    msg = msg.replace("\n", " ")
    esc = _applescript_string_literal(msg)
    script = f'display alert "SMS Ripper" message "{esc}" as critical'
    r = subprocess.run(
        ["/usr/bin/osascript", "-e", script],
        check=False,
    )
    return int(r.returncode != 0)


if __name__ == "__main__":
    raise SystemExit(main())
