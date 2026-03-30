"""Tests for archive.first_archival_tag and archive_message."""

import sqlite3
from pathlib import Path

import pytest

import archive
import config
from reader import Message
from tests.conftest import populate_chat_db


def test_first_archival_tag_order(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(archive, "ARCHIVAL_TAGS", frozenset({"ALPHA", "BETA"}))
    assert archive.first_archival_tag(["BETA", "ALPHA"]) == "BETA"
    assert archive.first_archival_tag(["OTHER", "ALPHA"]) == "ALPHA"
    assert archive.first_archival_tag(["OTHER"]) is None


def test_archive_table_name():
    assert archive.archive_table_name("POLITICAL") == "POLITICAL_archive"


def test_register_chat_db_trigger_stubs_no_crash():
    conn = sqlite3.connect(":memory:")
    try:
        archive._register_chat_db_trigger_stubs(conn)
        conn.execute("CREATE TABLE t (x)")
        conn.execute(
            "CREATE TRIGGER tr BEFORE DELETE ON t BEGIN "
            "SELECT before_delete_attachment_path(1); END"
        )
        conn.execute(
            "CREATE TRIGGER tr2 AFTER DELETE ON t BEGIN "
            "SELECT after_delete_message_plugin(1); END"
        )
        conn.execute("INSERT INTO t VALUES (1)")
        conn.execute("DELETE FROM t WHERE x = 1")
    finally:
        conn.close()


def test_archive_message_dry_run(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "DRY_RUN", True)
    msg = Message(
        rowid=99,
        chat_id=1,
        chat_identifier="+1",
        sender="+1",
        text="x",
        is_from_me=False,
        date=None,
        attributes=["POLITICAL"],
    )
    assert archive.archive_message(msg) is True
    assert "archive" in msg.actions_taken


def test_archive_message_no_tag_returns_false():
    msg = Message(
        rowid=1,
        chat_id=1,
        chat_identifier="+1",
        sender="+1",
        text="x",
        is_from_me=False,
        date=None,
        attributes=["LEGIT"],
    )
    assert archive.archive_message(msg) is False


def test_archive_message_moves_row(monkeypatch: pytest.MonkeyPatch, chat_db_path: Path):
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setenv(archive.ENV_DAEMON_CYCLE_START, "2026-01-02T03:04:05Z")
    monkeypatch.setenv(archive.ENV_DAEMON_CYCLE_PID, "424242")
    ns = 1_700_000_000_000_000_000
    mid = populate_chat_db(chat_db_path, date_ns=ns, text="political text")

    msg = Message(
        rowid=mid,
        chat_id=1,
        chat_identifier="+15551234567",
        sender="+15551234567",
        text="political text",
        is_from_me=False,
        date=None,
        attributes=["POLITICAL"],
    )
    assert archive.archive_message(msg) is True

    conn = sqlite3.connect(chat_db_path)
    try:
        assert (
            conn.execute("SELECT COUNT(*) FROM message WHERE rowid = ?", (mid,)).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM POLITICAL_archive WHERE rowid = ?", (mid,)
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute("SELECT text FROM POLITICAL_archive WHERE rowid = ?", (mid,)).fetchone()[
                0
            ]
            == "political text"
        )
        assert conn.execute(
            "SELECT daemon_cycle_start, daemon_cycle_pid FROM POLITICAL_archive WHERE rowid = ?",
            (mid,),
        ).fetchone() == ("2026-01-02T03:04:05Z", "424242")
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM chat_message_join WHERE message_id = ?", (mid,)
            ).fetchone()[0]
            == 0
        )
    finally:
        conn.close()


def test_purge_live_message_removes_row_without_archive(
    monkeypatch: pytest.MonkeyPatch, chat_db_path: Path
):
    monkeypatch.setattr(config, "DRY_RUN", False)
    ns = 1_700_000_000_000_000_001
    mid = populate_chat_db(chat_db_path, date_ns=ns, text="You have unsubscribed.")

    msg = Message(
        rowid=mid,
        chat_id=1,
        chat_identifier="+15551234567",
        sender="+15551234567",
        text="You have unsubscribed.",
        is_from_me=False,
        date=None,
        attributes=["LEGIT"],
    )
    assert archive.purge_live_message(msg) is True

    conn = sqlite3.connect(chat_db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM message WHERE rowid = ?", (mid,)).fetchone()[0] == 0
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='POLITICAL_archive'"
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM chat_message_join WHERE message_id = ?", (mid,)
            ).fetchone()[0]
            == 0
        )
    finally:
        conn.close()
