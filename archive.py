# archive.py
"""
Copy qualifying messages into <TAG>_archive tables in chat.db, then remove the live row.

Only tags with ``archive_enabled`` in the catalog participate (see :func:`tag_catalog.archival_tags`).
Destination uses :data:`ARCHIVAL_TAG_PRIORITY` when several apply, else classifier attribute order.

Each archive table may include sms-ripper-only columns (see ``_ensure_archive_extra_columns``),
including ``classifier_attributes`` (JSON array of all classifier tags for that message).
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from typing import Final

import classifier
import config
import tag_catalog
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


# Primary archival tag for this repo’s default catalog (see ``tag_catalog.DEFAULT_TAG_ROWS``).
# Not a universal constant—change if your catalog uses a different key for the same role.
DEFAULT_ARCHIVE_KEY: Final[str] = "education"
CANONICAL_ARCHIVE_TABLE: Final[str] = "message_tags_archive"

ARCHIVAL_TAGS: Final[frozenset[str]] = frozenset({DEFAULT_ARCHIVE_KEY})

# When several archival tags apply, prefer these first (then fall back to classifier list order).
# Keeps ward/church and SoFi branded SMS out of the civic ``education`` table when both tag.
ARCHIVAL_TAG_PRIORITY: Final[tuple[str, ...]] = ("church", "sofi", "education")


def first_archival_tag(
    attributes: list[str], archival_tags: set[str] | None = None
) -> str | None:
    """First archival tag: :data:`ARCHIVAL_TAG_PRIORITY` wins, then attribute list order."""
    raw = archival_tags if archival_tags is not None else set(ARCHIVAL_TAGS)
    active = {tag_catalog.normalize_tag(str(t)) for t in raw}
    attr_list = [
        tag_catalog.normalize_tag(str(a))
        for a in attributes
        if a is not None and str(a).strip()
    ]
    attr_list = [a for a in attr_list if a]
    attr_set = set(attr_list)
    for preferred in ARCHIVAL_TAG_PRIORITY:
        p = tag_catalog.normalize_tag(preferred)
        if p in active and p in attr_set:
            return p
    for attr in attr_list:
        if attr in active:
            return attr
    return None


def archive_table_name(tag: str) -> str:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", tag):
        raise ValueError(f"Invalid archival tag for SQL identifier: {tag!r}")
    if tag == DEFAULT_ARCHIVE_KEY:
        return CANONICAL_ARCHIVE_TABLE
    return f"{tag}_archive"


def require_archive_table(conn: sqlite3.Connection, tag: str) -> str:
    """
    Return :func:`archive_table_name` for ``tag`` if that table exists in ``conn``.

    Raises ``RuntimeError`` immediately if the table is missing (no alternate names, no rename).
    """
    name = archive_table_name(tag)
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    if not row:
        raise RuntimeError(
            f"Archive table {name!r} is missing from the database. "
            "Create it with a normal archive run (see archive_message) or point CHAT_DB_PATH "
            "at a database that already has this table."
        )
    return name


def _quote_ident(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return '"' + name.replace('"', '""') + '"'


def _ensure_archive_table(conn: sqlite3.Connection, tag: str) -> str:
    tag_catalog.ensure_tag_catalog(conn)
    table = archive_table_name(tag)
    q = _quote_ident(table)
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if row:
        _ensure_archive_extra_columns(conn, table)
        return table
    conn.execute(f"CREATE TABLE {q} AS SELECT * FROM message WHERE 0=1")
    logger.info(f"[ARCHIVE] Created table {table} mirroring message schema")
    _ensure_archive_extra_columns(conn, table)
    return table


def _archive_columns_from_message(conn: sqlite3.Connection, archive_table: str) -> list[str]:
    """Names of columns to copy from `message` into the archive table (handles extra archive-only cols)."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*_archive", archive_table):
        raise ValueError(f"Unexpected archive table name: {archive_table!r}")
    msg_order = [r[1] for r in conn.execute("PRAGMA table_info(message)").fetchall()]
    arch_names = {r[1] for r in conn.execute(f"PRAGMA table_info({archive_table})").fetchall()}
    return [c for c in msg_order if c in arch_names]


# JSON: legacy list or ``{"attributes":[],"weights":{}}``. Not present on Apple `message` rows.
CLASSIFIER_ATTRIBUTES_COLUMN: Final[str] = "classifier_attributes"


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


def _ensure_classifier_attributes_column(conn: sqlite3.Connection, table: str) -> None:
    """Add JSON TEXT column for full classifier attribute list (archive-only)."""
    qtbl = _quote_ident(table)
    col = CLASSIFIER_ATTRIBUTES_COLUMN
    has = conn.execute(
        "SELECT 1 FROM pragma_table_info(?) WHERE name = ? LIMIT 1",
        (table, col),
    ).fetchone()
    if not has:
        conn.execute(f"ALTER TABLE {qtbl} ADD COLUMN {_quote_ident(col)} TEXT")


def _ensure_archive_extra_columns(conn: sqlite3.Connection, table: str) -> None:
    _ensure_daemon_cycle_columns(conn, table)
    _ensure_classifier_attributes_column(conn, table)


def _write_classifier_attributes(
    conn: sqlite3.Connection,
    table: str,
    rowid: int,
    attributes: list[str],
    weights: dict[str, float] | None = None,
) -> None:
    """Persist classifier tags and optional per-tag weights for an archived row."""
    qtbl = _quote_ident(table)
    qcol = _quote_ident(CLASSIFIER_ATTRIBUTES_COLUMN)
    blob = classifier.encode_classifier_blob(attributes, weights or {})
    conn.execute(
        f"UPDATE {qtbl} SET {qcol} = ? WHERE rowid = ?",
        (blob, rowid),
    )


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
    if config.DRY_RUN:
        tag = first_archival_tag(
            message.attributes, {tag_catalog.canonical_tag(DEFAULT_ARCHIVE_KEY)}
        )
        if tag is None:
            _warning("[ARCHIVE] No archival tag in attributes; skipping")
            return False
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
        archival_tags = tag_catalog.archival_tags(conn) or {
            tag_catalog.canonical_tag(DEFAULT_ARCHIVE_KEY)
        }
        tag = first_archival_tag(message.attributes, archival_tags)
        if tag is None:
            _warning("[ARCHIVE] No archival tag in attributes; skipping")
            return False
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
        _write_classifier_attributes(
            conn, tbl, message.rowid, message.attributes, message.attribute_weights
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
