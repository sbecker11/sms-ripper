# archive.py
"""
Copy qualifying messages into <TAG>_archive tables in chat.db, then remove the live row.

Only tags listed in ARCHIVAL_TAGS participate. The archive destination is chosen by the
first matching tag in message.attributes (classifier order).
"""

from __future__ import annotations

import logging
import re
import sqlite3
from typing import Final

import config
from reader import Message

logger = logging.getLogger("sms_agent")

# Tags that have an archive table named <TAG>_archive (extend as needed).
ARCHIVAL_TAGS: Final[frozenset[str]] = frozenset({"POLITICAL"})


def first_archival_tag(attributes: list[str]) -> str | None:
    """First attribute (in list order) that is configured for archiving."""
    for attr in attributes:
        if attr in ARCHIVAL_TAGS:
            return attr
    return None


def archive_table_name(tag: str) -> str:
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", tag):
        raise ValueError(f"Invalid archival tag for SQL identifier: {tag!r}")
    return f"{tag}_archive"


def _quote_ident(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return '"' + name.replace('"', '""') + '"'


def _ensure_archive_table(conn: sqlite3.Connection, tag: str) -> str:
    table = archive_table_name(tag)
    q = _quote_ident(table)
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if row:
        return table
    conn.execute(f"CREATE TABLE {q} AS SELECT * FROM message WHERE 0=1")
    logger.info(f"[ARCHIVE] Created table {table} mirroring message schema")
    return table


def _delete_message_row(conn: sqlite3.Connection, rowid: int) -> None:
    """Remove a message and typical join rows (best-effort across DB versions)."""
    join_tables = (
        "message_attachment_join",
        "chat_message_join",
        "chat_recoverable_message_join",
    )
    for t in join_tables:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (t,),
        ).fetchone()
        if exists:
            conn.execute(f"DELETE FROM {_quote_ident(t)} WHERE message_id = ?", (rowid,))
    conn.execute("DELETE FROM message WHERE rowid = ?", (rowid,))


def archive_message(message: Message) -> bool:
    """
    Copy message row into <first_archival_tag>_archive, then delete the live message row.
    Returns False if no archival tag applies or on error.
    """
    tag = first_archival_tag(message.attributes)
    if tag is None:
        logger.warning("[ARCHIVE] No archival tag in attributes; skipping")
        return False

    if config.DRY_RUN:
        logger.info(
            f"[DRY RUN] Would archive message rowid={message.rowid} into {archive_table_name(tag)}"
        )
        message.actions_taken.append("archive")
        return True

    db_path = config.CHAT_DB_PATH
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(db_path, timeout=30.0)
        tbl = _ensure_archive_table(conn, tag)
        qtbl = _quote_ident(tbl)

        if not conn.execute(
            "SELECT 1 FROM message WHERE rowid = ?", (message.rowid,)
        ).fetchone():
            logger.warning(
                f"[ARCHIVE] No message row with rowid={message.rowid} in message table"
            )
            return False

        if conn.execute(f"SELECT 1 FROM {qtbl} WHERE rowid = ?", (message.rowid,)).fetchone():
            logger.info(
                f"[ARCHIVE] rowid {message.rowid} already in {tbl}; copying skipped, removing live row"
            )
        else:
            conn.execute(
                f"INSERT INTO {qtbl} SELECT * FROM message WHERE rowid = ?",
                (message.rowid,),
            )
        _delete_message_row(conn, message.rowid)
        conn.commit()
        logger.info(
            f"[ARCHIVE] Archived rowid {message.rowid} to {tbl} and removed from message"
        )
        message.actions_taken.append("archive")
        return True
    except sqlite3.Error as e:
        logger.error(f"[ARCHIVE] Failed: {e}")
        if conn is not None:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
        return False
    finally:
        if conn is not None:
            conn.close()
