#!/usr/bin/env python3
"""Mark inbound unread rows read in Messages chat.db (is_read + date_read, plus backfill).

Also advances chat.last_read_message_timestamp per thread (Messages Dock badge often follows this,
not message.is_read alone). Use --no-sync-chat-read to skip.

Default: keep the K newest unread, mark older inbound as read. Options: --live, --diagnose,
--include-associated, --every-inbound. See --help. Poe: doit, bulk-mark-read, badge-diagnose."""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import archive  # noqa: E402
import config  # noqa: E402


def _message_columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(message)").fetchall()}


def _read_column(cols: set[str]) -> str | None:
    if "is_read" in cols:
        return "is_read"
    if "read" in cols:
        return "read"
    return None


def _set_mark_read_sql(cols: set[str], qread: str) -> str:
    """SET clause: read flag + date_read so Messages treats the row as actually read."""
    parts = [f"{qread} = 1"]
    if "date_read" in cols:
        parts.append("date_read = COALESCE(NULLIF(date_read, 0), date)")
    return ", ".join(parts)


def _where_inbound_unread(qread: str, cols: set[str], *, include_associated: bool) -> str:
    parts = [
        "IFNULL(is_from_me, 0) = 0",
        f"IFNULL({qread}, 0) = 0",
    ]
    if "associated_message_type" in cols and not include_associated:
        parts.append("IFNULL(associated_message_type, 0) = 0")
    return " AND ".join(parts)


def _resolve_where_unread(qread: str, cols: set[str], args: argparse.Namespace) -> str:
    """Normal mode skips tapbacks unless --include-associated; --every-inbound skips no row types."""
    if getattr(args, "every_inbound", False):
        return f"IFNULL(is_from_me, 0) = 0 AND IFNULL({qread}, 0) = 0"
    return _where_inbound_unread(qread, cols, include_associated=args.include_associated)


def _print_diagnose(conn: sqlite3.Connection, cols: set[str], read_col: str, qread: str) -> None:
    """Read-only counts so we can compare chat.db to the Dock badge."""
    print("=== --diagnose (read-only) ===", flush=True)
    mb = f"IFNULL(message.is_from_me, 0) = 0 AND IFNULL(message.{read_col}, 0) = 0"
    b = f"IFNULL(is_from_me, 0) = 0 AND IFNULL({qread}, 0) = 0"
    total = int(conn.execute(f"SELECT COUNT(*) FROM message WHERE {b}").fetchone()[0])
    print(f"Inbound unread rows (any type): {total}", flush=True)

    if "associated_message_type" in cols:
        plain = int(
            conn.execute(
                f"SELECT COUNT(*) FROM message WHERE {b} AND IFNULL(associated_message_type, 0) = 0"
            ).fetchone()[0]
        )
        rx = int(
            conn.execute(
                f"SELECT COUNT(*) FROM message WHERE {b} AND IFNULL(associated_message_type, 0) != 0"
            ).fetchone()[0]
        )
        print(f"  of which plain (assoc=0): {plain}", flush=True)
        print(f"  of which tapback/reaction (assoc!=0): {rx}", flush=True)

    for col, label in (
        ("is_system_message", "is_system_message"),
        ("is_service_message", "is_service_message"),
        ("is_auto_reply", "is_auto_reply"),
    ):
        if col in cols:
            n = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM message WHERE {b} AND IFNULL({col}, 0) != 0"
                ).fetchone()[0]
            )
            if n:
                print(f"  unread with {label}=1: {n}", flush=True)

    if "text" in cols:
        no_text = "(message.text IS NULL OR message.text = '')"
        q = f"SELECT COUNT(*) FROM message WHERE {mb} AND {no_text}"
        n_empty = int(conn.execute(q).fetchone()[0])
        print(f"  unread with no plain text: {n_empty}", flush=True)
        if "attributedBody" in cols:
            has_ab = (
                "message.attributedBody IS NOT NULL AND length(message.attributedBody) > 0"
            )
            n_ab = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM message WHERE {mb} AND {no_text} AND {has_ab}"
                ).fetchone()[0]
            )
            print(
                f"    ... and attributedBody set (modern macOS bodies): {n_ab}",
                flush=True,
            )

    try:
        n_chats = int(
            conn.execute(
                f"""
                SELECT COUNT(DISTINCT chat_message_join.chat_id) FROM chat_message_join
                JOIN message ON message.rowid = chat_message_join.message_id
                WHERE {mb}
                """
            ).fetchone()[0]
        )
        print(f"Chats with ≥1 inbound unread message: {n_chats}", flush=True)
    except sqlite3.Error as e:
        print(f"(Could not count chats: {e})", flush=True)

    try:
        info = conn.execute("PRAGMA table_info(chat)").fetchall()
        ccols = [row[1] for row in info]
        tail = "..." if len(ccols) > 25 else ""
        print(
            f"chat table columns ({len(ccols)}): {', '.join(ccols[:25])}{tail}",
            flush=True,
        )
    except sqlite3.Error as e:
        print(f"(Could not read chat schema: {e})", flush=True)

    try:
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat'"
        ).fetchone() and conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_message_join'"
        ).fetchone():
            ccols = {row[1] for row in conn.execute("PRAGMA table_info(chat)").fetchall()}
            if "last_read_message_timestamp" in ccols:
                lag = int(
                    conn.execute(
                        f"""
                        SELECT COUNT(*) FROM chat c
                        WHERE EXISTS (
                          SELECT 1 FROM chat_message_join j
                          JOIN message m ON m.rowid = j.message_id
                          WHERE j.chat_id = c.ROWID
                            AND NOT (
                              IFNULL(m.is_from_me, 0) = 0 AND IFNULL(m.{qread}, 0) = 0
                            )
                        )
                        AND IFNULL(c.last_read_message_timestamp, 0) < (
                          SELECT IFNULL(MAX(m2.date), 0) FROM chat_message_join j2
                          JOIN message m2 ON m2.rowid = j2.message_id
                          WHERE j2.chat_id = c.ROWID
                            AND NOT (
                              IFNULL(m2.is_from_me, 0) = 0 AND IFNULL(m2.{qread}, 0) = 0
                            )
                        )
                        """
                    ).fetchone()[0]
                )
                if lag:
                    print(
                        f"Chats where last_read_message_timestamp lags read state: {lag} "
                        f"(re-run bulk_mark_read without --no-sync-chat-read to fix; affects Dock badge).",
                        flush=True,
                    )
    except sqlite3.Error:
        pass

    try:
        n_orphan = _count_orphan_outbound_unread(conn, qread)
        if n_orphan:
            print(
                f"Outbound rows stuck unread & not in any chat (orphan): {n_orphan} "
                f"— can confuse the Messages list; try --fix-orphan-outbound-read.",
                flush=True,
            )
    except sqlite3.Error:
        pass

    try:
        n_joined = _count_joined_outbound_unread(conn, qread)
        if n_joined:
            print(
                f"Outbound rows in threads still is_read=0 (joined): {n_joined} "
                f"— Dock badge can follow these; try --fix-joined-outbound-read (see --help).",
                flush=True,
            )
    except sqlite3.Error:
        pass

    print(
        "If inbound unread is 0 here but Dock still disagrees, iCloud/iPhone can restate counts.",
        flush=True,
    )
    print(
        "The sidebar Unread filter can follow iCloud and not match this SQLite snapshot.",
        flush=True,
    )
    print("=== end diagnose ===", flush=True)


def _count_orphan_outbound_unread(conn: sqlite3.Connection, qread: str) -> int:
    """Outbound rows with is_read=0 that are not in chat_message_join (sync/schema leftovers)."""
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_message_join'"
    ).fetchone():
        return 0
    return int(
        conn.execute(
            f"""
            SELECT COUNT(*) FROM message
            WHERE IFNULL(is_from_me, 0) = 1 AND IFNULL({qread}, 0) = 0
              AND NOT EXISTS (
                SELECT 1 FROM chat_message_join j WHERE j.message_id = message.rowid
              )
            """
        ).fetchone()[0]
    )


def _run_orphan_outbound_fix(
    conn: sqlite3.Connection,
    cols: set[str],
    qread: str,
    args: argparse.Namespace,
) -> None:
    if not args.fix_orphan_outbound_read:
        return
    n = _count_orphan_outbound_unread(conn, qread)
    if args.dry_run:
        if n:
            print(
                f"[dry-run] Would fix {n} orphan outbound stuck-unread row(s).",
                flush=True,
            )
        return
    if n == 0:
        return
    updated = _fix_orphan_outbound_read(conn, cols, qread)
    if updated:
        print(
            f"Fixed {updated} orphan outbound stuck-unread row(s) "
            "(no chat_message_join link).",
            flush=True,
        )


def _fix_orphan_outbound_read(
    conn: sqlite3.Connection, cols: set[str], qread: str
) -> int:
    """Mark orphan outbound stuck-unread rows read (does not touch joined thread messages)."""
    set_clause = _set_mark_read_sql(cols, qread)
    cur = conn.execute(
        f"""
        UPDATE message SET {set_clause}
        WHERE IFNULL(is_from_me, 0) = 1 AND IFNULL({qread}, 0) = 0
          AND NOT EXISTS (
            SELECT 1 FROM chat_message_join j WHERE j.message_id = message.rowid
          )
        """
    )
    conn.commit()
    return cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0


def _count_joined_outbound_unread(conn: sqlite3.Connection, qread: str) -> int:
    """Outbound is_read=0 rows that are linked to a chat (not orphans)."""
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_message_join'"
    ).fetchone():
        return 0
    return int(
        conn.execute(
            f"""
            SELECT COUNT(*) FROM message m
            WHERE IFNULL(m.is_from_me, 0) = 1 AND IFNULL(m.{qread}, 0) = 0
              AND EXISTS (
                SELECT 1 FROM chat_message_join j WHERE j.message_id = m.rowid
              )
            """
        ).fetchone()[0]
    )


def _fix_joined_outbound_read(
    conn: sqlite3.Connection, cols: set[str], qread: str
) -> tuple[int, set[int]]:
    """
    Mark joined outbound stuck-unread rows read. May affect read-receipt / delivery UI for those
    messages; use when the Dock badge disagrees with inbound unread counts.
    """
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_message_join'"
    ).fetchone():
        return 0, set()
    rowids = [
        int(r[0])
        for r in conn.execute(
            f"""
            SELECT m.rowid FROM message m
            WHERE IFNULL(m.is_from_me, 0) = 1 AND IFNULL(m.{qread}, 0) = 0
              AND EXISTS (
                SELECT 1 FROM chat_message_join j WHERE j.message_id = m.rowid
              )
            """
        ).fetchall()
    ]
    if not rowids:
        return 0, set()
    set_clause = _set_mark_read_sql(cols, qread)
    ph = ",".join("?" * len(rowids))
    conn.execute(
        f"UPDATE message SET {set_clause} WHERE rowid IN ({ph})",
        rowids,
    )
    conn.commit()
    chat_ids = _distinct_chat_ids_for_messages(conn, rowids)
    return len(rowids), chat_ids


def _run_joined_outbound_fix(
    conn: sqlite3.Connection,
    cols: set[str],
    qread: str,
    args: argparse.Namespace,
) -> None:
    if not args.fix_joined_outbound_read:
        return
    n = _count_joined_outbound_unread(conn, qread)
    if args.dry_run:
        if n:
            print(
                f"[dry-run] Would fix {n} joined outbound stuck-unread row(s) "
                "(your sends in threads).",
                flush=True,
            )
        return
    if n == 0:
        return
    updated, chat_ids = _fix_joined_outbound_read(conn, cols, qread)
    if updated:
        print(
            f"Fixed {updated} joined outbound stuck-unread row(s) "
            "(outbound messages in chat_message_join).",
            flush=True,
        )
    if (
        updated
        and not args.no_sync_chat_read
        and conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat'"
        ).fetchone()
    ):
        n_sync = _sync_chat_last_read_timestamps(conn, qread, chat_ids)
        if n_sync > 0:
            print(
                f"Synced chat.last_read_message_timestamp ({n_sync} chat row(s)) "
                "after joined-outbound fix.",
                flush=True,
            )


def _report_leftover_unread(conn: sqlite3.Connection, cols: set[str], qread: str) -> None:
    base = f"IFNULL(is_from_me, 0) = 0 AND IFNULL({qread}, 0) = 0"
    total = int(conn.execute(f"SELECT COUNT(*) FROM message WHERE {base}").fetchone()[0])
    if "associated_message_type" not in cols:
        print(f"Still unread (inbound): {total}")
        return
    plain = int(
        conn.execute(
            f"SELECT COUNT(*) FROM message WHERE {base} AND IFNULL(associated_message_type, 0) = 0"
        ).fetchone()[0]
    )
    reactions = int(
        conn.execute(
            f"SELECT COUNT(*) FROM message WHERE {base} AND IFNULL(associated_message_type, 0) != 0"
        ).fetchone()[0]
    )
    print(
        f"Still unread: {total} total ({plain} plain, {reactions} reaction/tapback). "
        "Tip: --include-associated or --every-inbound; 0 here but Dock≠0 → iCloud/iPhone."
    )


def _distinct_chat_ids_for_messages(
    conn: sqlite3.Connection, message_rowids: list[int]
) -> set[int]:
    if not message_rowids:
        return set()
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_message_join'"
    ).fetchone():
        return set()
    ph = ",".join("?" * len(message_rowids))
    rows = conn.execute(
        f"SELECT DISTINCT chat_id FROM chat_message_join WHERE message_id IN ({ph})",
        message_rowids,
    ).fetchall()
    return {int(r[0]) for r in rows}


def _sync_chat_last_read_timestamps(
    conn: sqlite3.Connection,
    qread: str,
    chat_rowids: set[int] | None,
) -> int:
    """
    Set chat.last_read_message_timestamp to the latest message.date that is outbound or
    inbound-read. Matches how unread should clear when message rows are marked read.
    If chat_rowids is None, update every chat that has at least one such message.
    """
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat'"
    ).fetchone():
        return 0
    chat_cols = {row[1] for row in conn.execute("PRAGMA table_info(chat)").fetchall()}
    if "last_read_message_timestamp" not in chat_cols:
        return 0
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_message_join'"
    ).fetchone():
        return 0

    read_ok = (
        f"NOT (IFNULL(m.is_from_me, 0) = 0 AND IFNULL(m.{qread}, 0) = 0)"
    )
    sub_select = f"""
        SELECT IFNULL(MAX(m.date), 0)
        FROM chat_message_join j
        JOIN message m ON m.rowid = j.message_id
        WHERE j.chat_id = chat.ROWID
          AND ({read_ok})
    """
    exists_ok = f"""
        EXISTS (
          SELECT 1 FROM chat_message_join j
          JOIN message m ON m.rowid = j.message_id
          WHERE j.chat_id = chat.ROWID
            AND ({read_ok})
        )
    """
    before = conn.total_changes
    if chat_rowids:
        ph = ",".join("?" * len(chat_rowids))
        conn.execute(
            f"""
            UPDATE chat SET last_read_message_timestamp = ({sub_select})
            WHERE ROWID IN ({ph})
              AND {exists_ok}
            """,
            tuple(chat_rowids),
        )
    else:
        conn.execute(
            f"""
            UPDATE chat SET last_read_message_timestamp = ({sub_select})
            WHERE {exists_ok}
            """
        )
    conn.commit()
    return conn.total_changes - before


def _backfill_date_read(conn: sqlite3.Connection, cols: set[str], qread: str) -> int:
    """
    Rows with is_read=1 but date_read still 0/NULL confuse Messages; fix them in one pass.
    Returns sqlite rowcount (may be -1 on some builds; treat as informational).
    """
    if "date_read" not in cols:
        return 0
    cur = conn.execute(
        f"""
        UPDATE message SET date_read = COALESCE(NULLIF(date_read, 0), date)
        WHERE IFNULL(is_from_me, 0) = 0
          AND IFNULL({qread}, 0) = 1
          AND (date_read IS NULL OR date_read = 0)
          AND date IS NOT NULL
        """
    )
    conn.commit()
    return cur.rowcount


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Mark older unread inbound messages read; keep the newest K unread."
    )
    parser.add_argument(
        "--keep-unread",
        type=int,
        default=10,
        metavar="K",
        help="Leave this many newest unread inbound messages unread (default: 10).",
    )
    parser.add_argument(
        "--max-mark",
        type=int,
        default=50_000,
        metavar="N",
        help="Safety cap: mark at most N messages this run (default: 50000).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print counts only; do not write chat.db.",
    )
    parser.add_argument(
        "--chat-db",
        metavar="PATH",
        default=None,
        help="Override chat.db path (default: CHAT_DB_PATH from .env).",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Mark one row at a time with a pause so the Dock unread count can step down visibly (Messages should be open).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.12,
        metavar="SEC",
        help="Seconds to wait after each row in --live mode (default: 0.12).",
    )
    parser.add_argument(
        "--include-associated",
        action="store_true",
        help="Also mark tapbacks/reactions (associated_message_type != 0). Use if the Dock badge stays high after a normal run.",
    )
    parser.add_argument(
        "--every-inbound",
        action="store_true",
        help="Mark every inbound unread row (widest net). Use if --include-associated is not enough.",
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="Print unread breakdown from chat.db and exit (read-only; does not modify the DB).",
    )
    parser.add_argument(
        "--no-sync-chat-read",
        action="store_true",
        help="Do not update chat.last_read_message_timestamp (Dock badge may stay high).",
    )
    parser.add_argument(
        "--fix-orphan-outbound-read",
        action="store_true",
        help=(
            "Mark read on outbound messages that are is_read=0 and not linked to any chat "
            "(chat_message_join). Safe cleanup for common sync leftovers; does not change "
            "outbound rows that belong to a conversation thread."
        ),
    )
    parser.add_argument(
        "--fix-joined-outbound-read",
        action="store_true",
        help=(
            "Mark read on your own messages that are still is_read=0 but are in a thread "
            "(chat_message_join). Use when inbound unread is 0 but the Dock badge stays >0; "
            "may change how read receipts / delivery appear for those sends."
        ),
    )
    args = parser.parse_args()

    if args.diagnose and args.live:
        print("Cannot combine --diagnose and --live.", file=sys.stderr)
        return 1
    if args.live and args.dry_run:
        print("Cannot combine --live and --dry-run.", file=sys.stderr)
        return 1
    if args.live and args.delay < 0:
        print("--delay must be >= 0", file=sys.stderr)
        return 1

    if args.keep_unread < 0:
        print("--keep-unread must be >= 0", file=sys.stderr)
        return 1

    db_path = args.chat_db if args.chat_db else config.CHAT_DB_PATH
    if not Path(db_path).expanduser().is_file():
        print(f"chat.db not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path, timeout=30.0)
    archive._register_chat_db_trigger_stubs(conn)
    try:
        cols = _message_columns(conn)
        read_col = _read_column(cols)
        if read_col is None:
            print("message table has no is_read/read column.", file=sys.stderr)
            return 1

        qread = '"' + read_col.replace('"', '""') + '"'

        if args.diagnose:
            _print_diagnose(conn, cols, read_col, qread)
            return 0

        where_unread = _resolve_where_unread(qread, cols, args)

        cur = conn.execute(f"SELECT COUNT(*) FROM message WHERE {where_unread}")
        n_unread = int(cur.fetchone()[0])

        set_clause = _set_mark_read_sql(cols, qread)

        if n_unread <= args.keep_unread:
            print(
                f"Inbound unread: {n_unread} (already ≤ keep-unread={args.keep_unread})."
            )
            if not args.dry_run and "date_read" in cols:
                bf = _backfill_date_read(conn, cols, qread)
                if bf and bf > 0:
                    print(f"Backfilled date_read on {bf} inbound row(s) already marked read.")
            if (
                not args.dry_run
                and not args.no_sync_chat_read
                and n_unread == 0
            ):
                n_sync = _sync_chat_last_read_timestamps(conn, qread, None)
                if n_sync > 0:
                    print(
                        f"Synced chat.last_read_message_timestamp ({n_sync} chat row(s)) "
                        "— Messages Dock badge often uses this.",
                        flush=True,
                    )
            _run_orphan_outbound_fix(conn, cols, qread, args)
            _run_joined_outbound_fix(conn, cols, qread, args)
            _report_leftover_unread(conn, cols, qread)
            return 0

        to_mark = min(n_unread - args.keep_unread, args.max_mark)
        rowids = [
            r[0]
            for r in conn.execute(
                f"SELECT rowid FROM message WHERE {where_unread} ORDER BY date ASC LIMIT ?",
                (to_mark,),
            ).fetchall()
        ]

        print(
            f"Inbound unread: {n_unread} · will mark read: {len(rowids)} "
            f"· leaving newest {args.keep_unread} unread"
            + (" (dry-run)" if args.dry_run else "")
        )

        if args.dry_run or not rowids:
            if args.dry_run:
                _run_orphan_outbound_fix(conn, cols, qread, args)
                _run_joined_outbound_fix(conn, cols, qread, args)
                _report_leftover_unread(conn, cols, qread)
            return 0

        n = len(rowids)
        if args.live:
            print(
                f"Live mode: {n} update(s), {args.delay}s pause each — "
                "keep Messages open and watch the Dock badge.",
                flush=True,
            )
            for i, rid in enumerate(rowids, start=1):
                conn.execute(
                    f"UPDATE message SET {set_clause} WHERE rowid = ? AND IFNULL(is_from_me, 0) = 0",
                    (rid,),
                )
                conn.commit()
                # One line so the terminal shows motion while the Dock ticks down.
                print(f"  marked {i}/{n}", flush=True)
                if args.delay > 0:
                    time.sleep(args.delay)
        else:
            chunk = 400
            for i in range(0, len(rowids), chunk):
                part = rowids[i : i + chunk]
                ph = ",".join("?" * len(part))
                conn.execute(
                    f"UPDATE message SET {set_clause} WHERE rowid IN ({ph}) AND IFNULL(is_from_me, 0) = 0",
                    part,
                )
            conn.commit()
        bf = 0
        if "date_read" in cols:
            bf = _backfill_date_read(conn, cols, qread)
        n_sync = 0
        if not args.no_sync_chat_read:
            chat_ids = _distinct_chat_ids_for_messages(conn, rowids)
            n_sync = _sync_chat_last_read_timestamps(conn, qread, chat_ids)
        msg = f"Done — marked {len(rowids)} read"
        if bf and bf > 0:
            msg += f"; backfilled date_read on {bf} more row(s)"
        if n_sync > 0:
            msg += (
                f"; synced chat read pointers ({n_sync} chat row(s)) "
                "for Dock badge"
            )
        print(msg + ".")
        _run_orphan_outbound_fix(conn, cols, qread, args)
        _run_joined_outbound_fix(conn, cols, qread, args)
        _report_leftover_unread(conn, cols, qread)
        return 0
    except sqlite3.Error as e:
        print(f"SQLite error: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
