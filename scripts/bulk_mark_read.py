#!/usr/bin/env python3
"""
Mark inbound unread iMessage rows read in chat.db so the Dock badge can drop.

Leaves the N *newest* unread messages still unread (default 10); marks older unread as read.
Run with Messages open so the app can refresh the badge (behavior varies by macOS).

  python scripts/bulk_mark_read.py --keep-unread 10
  python scripts/bulk_mark_read.py --keep-unread 10 --dry-run
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
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
    args = parser.parse_args()

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

        parts = [
            "IFNULL(is_from_me, 0) = 0",
            f"IFNULL({qread}, 0) = 0",
        ]
        if "associated_message_type" in cols:
            parts.append("IFNULL(associated_message_type, 0) = 0")

        where_unread = " AND ".join(parts)

        cur = conn.execute(f"SELECT COUNT(*) FROM message WHERE {where_unread}")
        n_unread = int(cur.fetchone()[0])

        if n_unread <= args.keep_unread:
            print(
                f"Inbound unread (text-style rows): {n_unread}; "
                f"nothing to do (already <= keep-unread={args.keep_unread})."
            )
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
            return 0

        qcol = qread
        chunk = 400
        for i in range(0, len(rowids), chunk):
            part = rowids[i : i + chunk]
            ph = ",".join("?" * len(part))
            conn.execute(
                f"UPDATE message SET {qcol} = 1 WHERE rowid IN ({ph}) AND IFNULL(is_from_me, 0) = 0",
                part,
            )
        conn.commit()
        print(
            f"Updated is_read for {len(rowids)} message(s). "
            "If the Dock badge lags, focus Messages or restart it."
        )
        return 0
    except sqlite3.Error as e:
        print(f"SQLite error: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
