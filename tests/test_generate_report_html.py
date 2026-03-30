"""Tests for generate_report_html.py."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "scripts" / "generate_report_html.py"


def test_generate_report_html_minimal_db(tmp_path: Path):
    db = tmp_path / "chat.db"
    out = tmp_path / "out.html"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE handle (rowid INTEGER PRIMARY KEY, id TEXT)")
    conn.execute("INSERT INTO handle (rowid, id) VALUES (1, '+15551234567')")
    conn.execute(
        "CREATE TABLE POLITICAL_archive ("
        "rowid INTEGER PRIMARY KEY, date INTEGER, text TEXT, handle_id INTEGER, "
        "daemon_cycle_start TEXT, daemon_cycle_pid TEXT)"
    )
    conn.execute(
        "INSERT INTO POLITICAL_archive (rowid, date, text, handle_id, daemon_cycle_start, daemon_cycle_pid) "
        "VALUES (1, 1000000000000000000, 'hello archive', 1, '2026-03-01T12:00:00Z', '99')"
    )
    conn.commit()
    conn.close()

    r = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--chat-db",
            str(db),
            "--output",
            str(out),
        ],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr + r.stdout
    text = out.read_text(encoding="utf-8")
    assert "Political archive report" in text
    assert "hello archive" in text
    assert "+15551234567" in text or "1555" in text
    assert "daemon-cycles/cycle_2026-03-01T12-00-00Z_99.html" in text
    assert "2026-03-01T12:00:00Z" in text
    assert "ts-dual" in text
    assert "2032-09-09" in text
    assert "01:46:40 UTC" in text
    assert "col-datetime" in text
    assert "<br" in text
    assert 'data-utc="2032-09-09T01:46:40Z"' in text
    assert "sms-ripper-top-bar" in text
    assert "sms-ripper-theme-bar" in text
    assert "sms-ripper-tz-bar" in text
    assert "smsRipperTheme" in text
    assert "theme-toggle-btn" in text
    assert "tz-toggle-btn" in text
    assert 'class="icon-nav"' in text
    assert "<svg " in text and "viewBox=\"0 0 16 16\"" in text
    assert "smsRipperOpenArchiveFull" in text
    assert "SMS_RIPPER_ARCHIVE_FULL" in text
    assert "text-full-open" in text
    assert ">full</th>" in text
    assert '"1": "hello archive"' in text
