# Shared fixtures for sms-ripper tests.

import sqlite3
from pathlib import Path

import pytest

import config


@pytest.fixture
def chat_db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    Minimal chat.db schema matching reader.get_recent_messages query.
    """
    db_path = tmp_path / "chat.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE handle (
                id TEXT
            );
            CREATE TABLE chat (
                chat_identifier TEXT NOT NULL
            );
            CREATE TABLE message (
                text TEXT,
                is_from_me INTEGER NOT NULL DEFAULT 0,
                date INTEGER NOT NULL,
                associated_message_type INTEGER NOT NULL DEFAULT 0,
                handle_id INTEGER
            );
            CREATE TABLE chat_message_join (
                message_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL
            );
            """
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(config, "CHAT_DB_PATH", str(db_path))
    return db_path


def populate_chat_db(
    db_path: Path,
    *,
    text: str = "hello",
    is_from_me: int = 0,
    date_ns: int,
    chat_identifier: str = "+15551234567",
    sender: str = "+15551234567",
    associated_message_type: int = 0,
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO handle (id) VALUES (?)", (sender,))
        handle_rowid = cur.lastrowid
        cur.execute(
            "INSERT INTO chat (chat_identifier) VALUES (?)", (chat_identifier,)
        )
        chat_rowid = cur.lastrowid
        cur.execute(
            """
            INSERT INTO message (text, is_from_me, date, associated_message_type, handle_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (text, is_from_me, date_ns, associated_message_type, handle_rowid),
        )
        msg_rowid = cur.lastrowid
        cur.execute(
            "INSERT INTO chat_message_join (message_id, chat_id) VALUES (?, ?)",
            (msg_rowid, chat_rowid),
        )
        conn.commit()
    finally:
        conn.close()
