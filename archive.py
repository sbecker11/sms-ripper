# archive.py
"""
Copy qualifying messages into <TAG>_archive tables in chat.db, then remove the live row.

Only tags listed in ARCHIVAL_TAGS participate. The archive destination is chosen by the
first matching tag in message.attributes (classifier order).
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from typing import Final

import config
from reader import Message

logger = logging.getLogger("sms_agent")

# Set by scripts/daemon_cycle.py only for the main_political subprocess; read in archive_message.
ENV_DAEMON_CYCLE_START = "SMS_RIPPER_DAEMON_CYCLE_START"
ENV_DAEMON_CYCLE_PID = "SMS_RIPPER_DAEMON_CYCLE_PID"


def _info(msg: str) -> None:
    if getattr(config, "QUIET", False):
        logger.debug(msg)
    else:
        logger.info(msg)


def _warning(msg: str) -> None:
    if getattr(config, "QUIET", False):
        logger.debug(msg)
    else:
        logger.warning(msg)


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
        _ensure_daemon_cycle_columns(conn, table)
        return table
    conn.execute(f"CREATE TABLE {q} AS SELECT * FROM message WHERE 0=1")
    logger.info(f"[ARCHIVE] Created table {table} mirroring message schema")
    _ensure_daemon_cycle_columns(conn, table)
    return table


def _archive_columns_from_message(conn: sqlite3.Connection, archive_table: str) -> list[str]:
    """Names of columns to copy from `message` into the archive table (handles extra archive-only cols)."""
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*_archive", archive_table):
        raise ValueError(f"Unexpected archive table name: {archive_table!r}")
    msg_order = [r[1] for r in conn.execute("PRAGMA table_info(message)").fetchall()]
    arch_names = {r[1] for r in conn.execute(f"PRAGMA table_info({archive_table})").fetchall()}
    return [c for c in msg_order if c in arch_names]


def _ensure_daemon_cycle_columns(conn: sqlite3.Connection, table: str) -> None:
    """Add optional columns used to link archive rows to reports/daemon-cycles/cycle_*.html."""
    qtbl = _quote_ident(table)
    for col, typ in (("daemon_cycle_start", "TEXT"), ("daemon_cycle_pid", "TEXT")):
        qcol = _quote_ident(col)
        has = conn.execute(
            "SELECT 1 FROM pragma_table_info(?) WHERE name = ? LIMIT 1",
            (table, col),
        ).fetchone()
        if not has:
            conn.execute(f"ALTER TABLE {qtbl} ADD COLUMN {qcol} {typ}")


def _register_chat_db_trigger_stubs(conn: sqlite3.Connection) -> None:
    """
    Messages.app registers SQLite functions used in DELETE triggers on chat.db.
    A plain Python sqlite3 connection does not define them, so DELETE FROM message
    can fail with "no such function: before_delete_attachment_path".
    Stubs satisfy the trigger; behavior matches typical no-op cleanup when Messages is quit.
    """
    noop_names = (
        "before_delete_attachment_path",
        "after_delete_message",
        "after_delete_message_plugin",
        "delete_attachment_path",
    )

    def _noop(*_args: object) -> None:
        return None

    for name in noop_names:
        try:
            conn.create_function(name, -1, _noop)
        except (sqlite3.OperationalError, TypeError, AttributeError) as e:
            logger.warning("[ARCHIVE] Could not register SQL stub %r: %s", name, e)


def purge_live_message(message: Message) -> bool:
    """
    Remove the message row from chat.db (and typical join rows) without copying to any *_archive table.
    Use for unsubscribe confirmations and similar junk you do not want preserved.
    """
    if config.DRY_RUN:
        _info(f"[DRY RUN] Would purge (delete) message rowid={message.rowid} from message (no archive)")
        message.actions_taken.append("purge")
        return True

    db_path = config.CHAT_DB_PATH
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(db_path, timeout=30.0)
        _register_chat_db_trigger_stubs(conn)
        if not conn.execute(
            "SELECT 1 FROM message WHERE rowid = ?", (message.rowid,)
        ).fetchone():
            _warning(f"[PURGE] No message row with rowid={message.rowid} in message table")
            return False
        _delete_message_row(conn, message.rowid)
        conn.commit()
        _info(f"[PURGE] Removed rowid {message.rowid} from message (not archived)")
        message.actions_taken.append("purge")
        return True
    except sqlite3.Error as e:
        logger.error(f"[PURGE] Failed: {e}")
        if conn is not None:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
        return False
    finally:
        if conn is not None:
            conn.close()


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
        _warning("[ARCHIVE] No archival tag in attributes; skipping")
        return False

    if config.DRY_RUN:
        _info(
            f"[DRY RUN] Would archive message rowid={message.rowid} into {archive_table_name(tag)}"
        )
        message.actions_taken.append("archive")
        return True

    db_path = config.CHAT_DB_PATH
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(db_path, timeout=30.0)
        _register_chat_db_trigger_stubs(conn)
        tbl = _ensure_archive_table(conn, tag)
        qtbl = _quote_ident(tbl)

        if not conn.execute(
            "SELECT 1 FROM message WHERE rowid = ?", (message.rowid,)
        ).fetchone():
            _warning(
                f"[ARCHIVE] No message row with rowid={message.rowid} in message table"
            )
            return False

        if conn.execute(f"SELECT 1 FROM {qtbl} WHERE rowid = ?", (message.rowid,)).fetchone():
            _info(
                f"[ARCHIVE] rowid {message.rowid} already in {tbl}; copying skipped, removing live row"
            )
        else:
            cols = _archive_columns_from_message(conn, tbl)
            if not cols:
                logger.error("[ARCHIVE] No overlapping columns between message and %s", tbl)
                return False
            quoted = ", ".join(_quote_ident(c) for c in cols)
            conn.execute(
                f"INSERT INTO {qtbl} ({quoted}) SELECT {quoted} FROM message WHERE rowid = ?",
                (message.rowid,),
            )
            c_start = os.environ.get(ENV_DAEMON_CYCLE_START)
            c_pid = os.environ.get(ENV_DAEMON_CYCLE_PID)
            if c_start and c_pid:
                conn.execute(
                    f"UPDATE {qtbl} SET daemon_cycle_start = ?, daemon_cycle_pid = ? "
                    "WHERE rowid = ?",
                    (c_start, c_pid, message.rowid),
                )
        _delete_message_row(conn, message.rowid)
        conn.commit()
        _info(
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
