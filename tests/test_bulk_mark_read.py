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
        "date INTEGER, is_read INTEGER, is_from_me INTEGER, associated_message_type INTEGER, "
        "date_read INTEGER DEFAULT 0)"
    )
    # Oldest unread inbound first in time order (ascending date)
    for d in (100, 200, 300, 400, 500):
        conn.execute(
            "INSERT INTO message (date, is_read, is_from_me, associated_message_type, date_read) "
            "VALUES (?, 0, 0, 0, 0)",
            (d,),
        )
    conn.commit()
    conn.close()

    r = _run_script(db, "--keep-unread", "2")
    assert r.returncode == 0, r.stderr
    assert "will mark read: 3" in r.stdout

    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT date, is_read, date_read FROM message ORDER BY date"
    ).fetchall()
    conn.close()
    # Newest two (400, 500) stay unread; 100,200,300 marked read with date_read filled
    assert rows == [
        (100, 1, 100),
        (200, 1, 200),
        (300, 1, 300),
        (400, 0, 0),
        (500, 0, 0),
    ]


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
    assert "keep-unread=5" in r.stdout


def test_bulk_mark_read_live_mode(tmp_path: Path):
    db = tmp_path / "chat.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE message (rowid INTEGER PRIMARY KEY AUTOINCREMENT, "
        "date INTEGER, is_read INTEGER, is_from_me INTEGER, associated_message_type INTEGER, "
        "date_read INTEGER DEFAULT 0)"
    )
    for d in (100, 200, 300, 400, 500):
        conn.execute(
            "INSERT INTO message (date, is_read, is_from_me, associated_message_type, date_read) "
            "VALUES (?, 0, 0, 0, 0)",
            (d,),
        )
    conn.commit()
    conn.close()

    r = _run_script(
        db, "--keep-unread", "2", "--live", "--delay", "0"
    )
    assert r.returncode == 0, r.stderr
    assert "Live mode" in r.stdout
    assert "marked 3/3" in r.stdout

    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT date, is_read, date_read FROM message ORDER BY date"
    ).fetchall()
    conn.close()
    assert rows == [
        (100, 1, 100),
        (200, 1, 200),
        (300, 1, 300),
        (400, 0, 0),
        (500, 0, 0),
    ]


def test_bulk_mark_read_backfills_date_read_when_already_is_read(tmp_path: Path):
    """Prior run set is_read only; backfill fixes date_read so Messages can update the badge."""
    db = tmp_path / "chat.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE message (rowid INTEGER PRIMARY KEY AUTOINCREMENT, "
        "date INTEGER, is_read INTEGER, is_from_me INTEGER, associated_message_type INTEGER, "
        "date_read INTEGER DEFAULT 0)"
    )
    conn.execute(
        "INSERT INTO message (date, is_read, is_from_me, associated_message_type, date_read) "
        "VALUES (999, 1, 0, 0, 0)"
    )
    conn.commit()
    conn.close()

    r = _run_script(db, "--keep-unread", "5")
    assert r.returncode == 0, r.stderr
    assert "keep-unread=5" in r.stdout
    assert "Backfilled date_read" in r.stdout

    conn = sqlite3.connect(db)
    dr = conn.execute("SELECT date_read FROM message").fetchone()[0]
    conn.close()
    assert dr == 999


def test_bulk_mark_read_include_associated_marks_reactions(tmp_path: Path):
    db = tmp_path / "chat.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE message (rowid INTEGER PRIMARY KEY AUTOINCREMENT, "
        "date INTEGER, is_read INTEGER, is_from_me INTEGER, associated_message_type INTEGER, "
        "date_read INTEGER DEFAULT 0)"
    )
    conn.execute(
        "INSERT INTO message (date, is_read, is_from_me, associated_message_type, date_read) "
        "VALUES (1, 0, 0, 0, 0)"
    )
    conn.execute(
        "INSERT INTO message (date, is_read, is_from_me, associated_message_type, date_read) "
        "VALUES (2, 0, 0, 1000, 0)"
    )
    conn.commit()
    conn.close()

    r = _run_script(db, "--keep-unread", "0", "--include-associated")
    assert r.returncode == 0, r.stderr
    assert "will mark read: 2" in r.stdout

    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM message WHERE is_read=0").fetchone()[0]
    conn.close()
    assert n == 0


def test_bulk_mark_read_diagnose_read_only(tmp_path: Path):
    db = tmp_path / "chat.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE message (date INTEGER, is_read INTEGER, is_from_me INTEGER, "
        "associated_message_type INTEGER, text TEXT)"
    )
    conn.execute(
        "INSERT INTO message (date, is_read, is_from_me, associated_message_type, text) "
        "VALUES (1, 0, 0, 0, 'hi')"
    )
    conn.commit()
    conn.close()

    r = _run_script(db, "--diagnose")
    assert r.returncode == 0, r.stderr
    assert "Inbound unread rows (any type): 1" in r.stdout

    conn = sqlite3.connect(db)
    unread = conn.execute("SELECT COUNT(*) FROM message WHERE is_read=0").fetchone()[0]
    conn.close()
    assert unread == 1
