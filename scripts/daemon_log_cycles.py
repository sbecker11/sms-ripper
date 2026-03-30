"""
Parse logs/daemon.log into daemon cycles (start/end markers share the same pid).

Used by generate_daemon_cycles_html.py; importable for tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# _log_line format: "{ts} ======== ... ========\n"
_RE_START = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) ======== cycle start pid=(\d+) ========$"
)
_RE_END_OK = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) ======== cycle end pid=(\d+) OK ========$"
)
_RE_END_ERR = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) ======== cycle end pid=(\d+) ERROR after (\w+) ========$"
)
# Older logs before pid appeared on cycle end lines
_RE_LEGACY_OK = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) ======== cycle end OK ========$"
)
_RE_LEGACY_ERR = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) ======== cycle end ERROR after (\w+) ========$"
)


@dataclass
class DaemonCycle:
    start_ts: str
    pid: str
    lines: list[str] = field(default_factory=list)
    end_ts: str | None = None
    status: str = "incomplete"  # ok | error | incomplete
    error_step: str | None = None

    def file_slug(self) -> str:
        return f"{self.start_ts.replace(':', '-')}_{self.pid}"


def parse_daemon_log_lines(lines: list[str]) -> list[DaemonCycle]:
    """Split log lines into cycles; same pid on start and end lines when using new format."""
    cycles: list[DaemonCycle] = []
    current: DaemonCycle | None = None
    preamble: list[str] = []

    def flush_incomplete() -> None:
        nonlocal current
        if current is not None:
            current.status = "incomplete"
            cycles.append(current)
            current = None

    i = 0
    while i < len(lines):
        raw = lines[i]
        s = raw.rstrip("\n\r")

        m_start = _RE_START.match(s)
        if m_start:
            flush_incomplete()
            current = DaemonCycle(start_ts=m_start.group(1), pid=m_start.group(2), lines=[raw])
            i += 1
            continue

        m_ok = _RE_END_OK.match(s)
        if m_ok and current is not None and m_ok.group(2) == current.pid:
            current.lines.append(raw)
            current.end_ts = m_ok.group(1)
            current.status = "ok"
            cycles.append(current)
            current = None
            i += 1
            continue

        m_err = _RE_END_ERR.match(s)
        if m_err and current is not None and m_err.group(2) == current.pid:
            current.lines.append(raw)
            current.end_ts = m_err.group(1)
            current.status = "error"
            current.error_step = m_err.group(3)
            cycles.append(current)
            current = None
            i += 1
            continue

        m_lo = _RE_LEGACY_OK.match(s)
        if m_lo and current is not None:
            current.lines.append(raw)
            current.end_ts = m_lo.group(1)
            current.status = "ok"
            cycles.append(current)
            current = None
            i += 1
            continue

        m_le = _RE_LEGACY_ERR.match(s)
        if m_le and current is not None:
            current.lines.append(raw)
            current.end_ts = m_le.group(1)
            current.status = "error"
            current.error_step = m_le.group(2)
            cycles.append(current)
            current = None
            i += 1
            continue

        if current is not None:
            current.lines.append(raw)
        else:
            preamble.append(raw)
        i += 1

    flush_incomplete()
    return cycles


def parse_daemon_log_text(text: str) -> list[DaemonCycle]:
    if not text:
        return []
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] = lines[-1] + "\n"
    return parse_daemon_log_lines(lines)
