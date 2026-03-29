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
        "CREATE TABLE POLITICAL_archive (rowid INTEGER PRIMARY KEY, date INTEGER, text TEXT, handle_id INTEGER)"
    )
    conn.execute(
        "INSERT INTO POLITICAL_archive (rowid, date, text, handle_id) VALUES (1, 1000000000000000000, 'hello archive', 1)"
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
