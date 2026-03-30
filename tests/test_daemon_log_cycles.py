"""Tests for daemon_log_cycles.parse_daemon_log_*"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from daemon_log_cycles import parse_daemon_log_text  # noqa: E402


def test_parse_new_format_matching_pid():
    log = """2026-03-29T22:20:01Z ======== cycle start pid=9497 ========
2026-03-29T22:20:01Z START backup: python
2026-03-29T22:20:14Z ======== cycle end pid=9497 OK ========
"""
    cycles = parse_daemon_log_text(log)
    assert len(cycles) == 1
    c = cycles[0]
    assert c.pid == "9497"
    assert c.status == "ok"
    assert c.start_ts == "2026-03-29T22:20:01Z"
    assert c.end_ts == "2026-03-29T22:20:14Z"
    assert "cycle start pid=9497" in "".join(c.lines)


def test_parse_error_end():
    log = """2026-03-29T10:00:00Z ======== cycle start pid=111 ========
2026-03-29T10:00:01Z FAIL backup exit=1
2026-03-29T10:00:02Z ======== cycle end pid=111 ERROR after backup ========
"""
    cycles = parse_daemon_log_text(log)
    assert len(cycles) == 1
    assert cycles[0].status == "error"
    assert cycles[0].error_step == "backup"


def test_parse_legacy_ok_closes_open_cycle():
    log = """2026-03-29T12:00:00Z ======== cycle start pid=222 ========
2026-03-29T12:00:05Z ======== cycle end OK ========
"""
    cycles = parse_daemon_log_text(log)
    assert len(cycles) == 1
    assert cycles[0].pid == "222"
    assert cycles[0].status == "ok"


def test_parse_two_cycles_newest_sort_helper():
    log = """2026-03-28T01:00:00Z ======== cycle start pid=1 ========
2026-03-28T01:00:01Z ======== cycle end pid=1 OK ========
2026-03-29T02:00:00Z ======== cycle start pid=2 ========
2026-03-29T02:00:02Z ======== cycle end pid=2 OK ========
"""
    cycles = parse_daemon_log_text(log)
    assert len(cycles) == 2
    assert cycles[0].pid == "1"
    assert cycles[1].pid == "2"
