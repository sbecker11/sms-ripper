"""Tests for archive.first_archival_tag and archive_message."""

import json
import sqlite3
from pathlib import Path

import pytest

import archive
import classifier
import config
from reader import Message
from tests.conftest import populate_chat_db


def test_first_archival_tag_order(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(archive, "ARCHIVAL_TAGS", frozenset({"ALPHA", "BETA"}))
    assert archive.first_archival_tag(["BETA", "ALPHA"]) == "beta"
    assert archive.first_archival_tag(["OTHER", "ALPHA"]) == "alpha"
    assert archive.first_archival_tag(["OTHER"]) is None


def test_first_archival_tag_church_before_education():
    archival = frozenset({"church", "education"})
    assert (
        archive.first_archival_tag(["education", "church"], archival_tags=set(archival))
        == "church"
    )
    assert (
        archive.first_archival_tag(["church", "education"], archival_tags=set(archival))
        == "church"
    )


def test_first_archival_tag_sofi_before_education():
    archival = frozenset({"sofi", "education"})
    assert (
        archive.first_archival_tag(["education", "sofi"], archival_tags=set(archival))
        == "sofi"
    )


def test_archive_table_name():
    assert archive.archive_table_name(archive.DEFAULT_ARCHIVE_KEY) == "message_tags_archive"


def test_require_archive_table_raises_when_missing():
    conn = sqlite3.connect(":memory:")
    try:
        with pytest.raises(RuntimeError, match="Archive table"):
            archive.require_archive_table(conn, "education")
    finally:
        conn.close()


def test_require_archive_table_returns_when_present():
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            f"CREATE TABLE {archive.CANONICAL_ARCHIVE_TABLE} (rowid INTEGER PRIMARY KEY)"
        )
        assert (
            archive.require_archive_table(conn, "education")
            == archive.CANONICAL_ARCHIVE_TABLE
        )
    finally:
        conn.close()


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
        attributes=["education"],
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
        attributes=["personal"],
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
        attributes=["education"],
    )
    assert archive.archive_message(msg) is True

    conn = sqlite3.connect(chat_db_path)
    try:
        tbl = archive.archive_table_name(archive.DEFAULT_ARCHIVE_KEY)
        assert (
            conn.execute("SELECT COUNT(*) FROM message WHERE rowid = ?", (mid,)).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                f"SELECT COUNT(*) FROM {tbl} WHERE rowid = ?", (mid,)
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(f"SELECT text FROM {tbl} WHERE rowid = ?", (mid,)).fetchone()[
                0
            ]
            == "political text"
        )
        assert conn.execute(
            f"SELECT daemon_cycle_start, daemon_cycle_pid FROM {tbl} WHERE rowid = ?",
            (mid,),
        ).fetchone() == ("2026-01-02T03:04:05Z", "424242")
        raw_attrs = conn.execute(
            f"SELECT {archive.CLASSIFIER_ATTRIBUTES_COLUMN} FROM {tbl} WHERE rowid = ?",
            (mid,),
        ).fetchone()[0]
        assert json.loads(raw_attrs) == json.loads(
            classifier.encode_classifier_blob(["education"], {"education": 1.0})
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM chat_message_join WHERE message_id = ?", (mid,)
            ).fetchone()[0]
            == 0
        )
    finally:
        conn.close()


def test_archive_message_stores_all_classifier_attributes(
    monkeypatch: pytest.MonkeyPatch, chat_db_path: Path
):
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.delenv(archive.ENV_DAEMON_CYCLE_START, raising=False)
    monkeypatch.delenv(archive.ENV_DAEMON_CYCLE_PID, raising=False)
    ns = 1_700_000_000_000_000_002
    mid = populate_chat_db(chat_db_path, date_ns=ns, text="bulk")

    attrs = ["education", "spam", "unknown"]
    msg = Message(
        rowid=mid,
        chat_id=1,
        chat_identifier="+15551234567",
        sender="+15551234567",
        text="bulk",
        is_from_me=False,
        date=None,
        attributes=attrs,
        attribute_weights={a: 1.0 for a in attrs},
    )
    assert archive.archive_message(msg) is True

    conn = sqlite3.connect(chat_db_path)
    try:
        tbl = archive.archive_table_name(archive.DEFAULT_ARCHIVE_KEY)
        raw = conn.execute(
            f"SELECT {archive.CLASSIFIER_ATTRIBUTES_COLUMN} FROM {tbl} WHERE rowid = ?",
            (mid,),
        ).fetchone()[0]
        assert json.loads(raw) == json.loads(
            classifier.encode_classifier_blob(attrs, {a: 1.0 for a in attrs})
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
        attributes=["personal"],
    )
    assert archive.purge_live_message(msg) is True

    conn = sqlite3.connect(chat_db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM message WHERE rowid = ?", (mid,)).fetchone()[0] == 0
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
                (archive.archive_table_name(archive.DEFAULT_ARCHIVE_KEY),),
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
