"""Tests for scripts/bulk_mark_read.py."""

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "scripts" / "bulk_mark_read.py"


def _run_script(db_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**__import__("os").environ, "PYTHONPATH": str(_REPO)}
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "--chat-db", str(db_path), *args],
        cwd=str(_REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_bulk_mark_read_keeps_newest_unread(tmp_path: Path):
    db = tmp_path / "chat.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE message (rowid INTEGER PRIMARY KEY AUTOINCREMENT, "
        "date INTEGER, is_read INTEGER, is_from_me INTEGER, associated_message_type INTEGER)"
    )
    # Oldest unread inbound first in time order (ascending date)
    for d in (100, 200, 300, 400, 500):
        conn.execute(
            "INSERT INTO message (date, is_read, is_from_me, associated_message_type) VALUES (?, 0, 0, 0)",
            (d,),
        )
    conn.commit()
    conn.close()

    r = _run_script(db, "--keep-unread", "2")
    assert r.returncode == 0, r.stderr
    assert "will mark read: 3" in r.stdout

    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT date, is_read FROM message ORDER BY date").fetchall()
    conn.close()
    # Newest two (400, 500) stay unread; 100,200,300 marked read
    assert rows == [(100, 1), (200, 1), (300, 1), (400, 0), (500, 0)]


def test_bulk_mark_read_dry_run_no_write(tmp_path: Path):
    db = tmp_path / "chat.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE message (date INTEGER, is_read INTEGER, is_from_me INTEGER, associated_message_type INTEGER)"
    )
    conn.execute(
        "INSERT INTO message (date, is_read, is_from_me, associated_message_type) VALUES (1, 0, 0, 0)"
    )
    conn.commit()
    conn.close()

    r = _run_script(db, "--keep-unread", "0", "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "dry-run" in r.stdout

    conn = sqlite3.connect(db)
    n = conn.execute("SELECT is_read FROM message").fetchone()[0]
    conn.close()
    assert n == 0


def test_bulk_mark_read_nothing_to_do(tmp_path: Path):
    db = tmp_path / "chat.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE message (date INTEGER, is_read INTEGER, is_from_me INTEGER, associated_message_type INTEGER)"
    )
    conn.execute(
        "INSERT INTO message (date, is_read, is_from_me, associated_message_type) VALUES (1, 0, 0, 0)"
    )
    conn.commit()
    conn.close()

    r = _run_script(db, "--keep-unread", "5")
    assert r.returncode == 0, r.stderr
    assert "nothing to do" in r.stdout
