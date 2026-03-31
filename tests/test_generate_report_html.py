"""Tests for generate_report_html.py."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "scripts" / "generate_report_html.py"
_SCRIPTS = _REPO / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import generate_report_html as grh  # noqa: E402


def test_generate_report_html_minimal_db(tmp_path: Path):
    db = tmp_path / "chat.db"
    out = tmp_path / "out.html"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE handle (rowid INTEGER PRIMARY KEY, id TEXT)")
    conn.execute("INSERT INTO handle (rowid, id) VALUES (1, '+15551234567')")
    conn.execute(
        "CREATE TABLE POLITICAL_archive ("
        "rowid INTEGER PRIMARY KEY, date INTEGER, text TEXT, handle_id INTEGER, "
        "daemon_cycle_start TEXT, daemon_cycle_pid TEXT, classifier_attributes TEXT)"
    )
    conn.execute(
        "INSERT INTO POLITICAL_archive (rowid, date, text, handle_id, daemon_cycle_start, daemon_cycle_pid, classifier_attributes) "
        "VALUES (1, 1000000000000000000, 'hello archive', 1, '2026-03-01T12:00:00Z', '99', '[\"POLITICAL\",\"SPAM\"]')"
    )
    conn.execute(
        "INSERT INTO POLITICAL_archive (rowid, date, text, handle_id, daemon_cycle_start, daemon_cycle_pid, classifier_attributes) "
        "VALUES (2, 1000000000000000001, '', 1, NULL, NULL, '[\"POLITICAL\",\"SPAM\"]')"
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
    assert "Message Archive Report" in text
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
    assert 'id="archive-type-filter"' in text
    assert "total row(s) in the archives" in text
    assert "Latest 100 archived messages (newest first)" in text
    assert "Archive type" in text
    assert "POLITICAL" in text and "SPAM" in text
    assert '<option value="UNKNOWN">UNKNOWN</option>' in text
    assert 'data-archive-no-plaintext="1"' in text
    assert 'getAttribute("data-archive-no-plaintext")' in text
    assert "archive-row" in text
    assert "data-archive-types" in text
    assert "archive-filter-count" in text
    assert "smsRipperArchiveTypeFilter" in text
    assert "archiveTypeFilterSetCookie" in text
    assert 'href="../CHANGELOG.md"' in text
    assert "footer-changelog" in text
    assert "latest entry" in text and " UTC" in text


def test_generate_report_html_archive_training_url(tmp_path: Path):
    db = tmp_path / "chat.db"
    out = tmp_path / "out.html"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE handle (rowid INTEGER PRIMARY KEY, id TEXT)")
    conn.execute("INSERT INTO handle (rowid, id) VALUES (1, '+15551234567')")
    conn.execute(
        "CREATE TABLE POLITICAL_archive ("
        "rowid INTEGER PRIMARY KEY, date INTEGER, text TEXT, handle_id INTEGER, "
        "classifier_attributes TEXT)"
    )
    conn.execute(
        "INSERT INTO POLITICAL_archive (rowid, date, text, handle_id, classifier_attributes) "
        "VALUES (1, 1000000000000000000, 'hello', 1, '[\"POLITICAL\"]')"
    )
    conn.commit()
    conn.close()

    base = "http://127.0.0.1:8765"
    r = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--chat-db",
            str(db),
            "--output",
            str(out),
            "--archive-training-url",
            base,
        ],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr + r.stdout
    text = out.read_text(encoding="utf-8")
    assert "SMS_RIPPER_ARCHIVE_TRAINING_SERVER" in text
    assert '"http://127.0.0.1:8765"' in text
    assert 'window.open(base + "/message/"' in text


def test_build_training_server_index_html(tmp_path: Path):
    db = tmp_path / "chat.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE handle (rowid INTEGER PRIMARY KEY, id TEXT)")
    conn.execute("INSERT INTO handle (rowid, id) VALUES (1, '+15551234567')")
    conn.execute(
        "CREATE TABLE POLITICAL_archive ("
        "rowid INTEGER PRIMARY KEY, date INTEGER, text TEXT, handle_id INTEGER, "
        "classifier_attributes TEXT)"
    )
    conn.execute(
        "INSERT INTO POLITICAL_archive (rowid, date, text, handle_id, classifier_attributes) "
        "VALUES (7, 1000000000000000000, 'hello archive', 1, '[\"POLITICAL\"]')"
    )
    conn.commit()
    html_doc = grh.build_training_server_index_html(conn, limit=10, db_path=str(db))
    conn.close()
    assert "Message Archive Report" in html_doc
    assert "hello archive" in html_doc
    assert "smsRipperOpenArchiveFull" in html_doc
    assert 'window.open("/message/"' in html_doc
    assert "SMS_RIPPER_ARCHIVE_FULL" not in html_doc
    assert "archive_training_server" in html_doc or "tag-training" in html_doc
    assert "smsRipperArchiveTypeFilter" in html_doc
    assert 'href="/CHANGELOG.md"' in html_doc
    assert "footer-changelog" in html_doc
    assert "latest entry" in html_doc and " UTC" in html_doc
    assert "Latest 10 archived messages (newest first)" in html_doc


def test_datetime_cell_inner_dash_becomes_open_full_button():
    out = grh._datetime_cell_inner_or_open_full("—", 42)
    assert "dash-open-full" in out
    assert "smsRipperOpenArchiveFull(42)" in out
    assert grh._datetime_cell_inner_or_open_full('<span class="dt-adjustable">x</span>', 42) == (
        '<span class="dt-adjustable">x</span>'
    )
