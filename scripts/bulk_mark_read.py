#!/usr/bin/env python3
"""Mark inbound unread rows read in Messages chat.db (is_read + date_read, plus backfill).

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

    print(
        "If inbound unread is 0 here but Dock shows a number, sync/iCloud is the usual cause.",
        flush=True,
    )
    print("=== end diagnose ===", flush=True)


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
        msg = f"Done — marked {len(rowids)} read"
        if bf and bf > 0:
            msg += f"; backfilled date_read on {bf} more row(s)"
        print(msg + ".")
        _report_leftover_unread(conn, cols, qread)
        return 0
    except sqlite3.Error as e:
        print(f"SQLite error: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
