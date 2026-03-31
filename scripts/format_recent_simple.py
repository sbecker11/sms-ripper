#!/usr/bin/env python3
"""
Print recent iMessage rows: datetime + source, then rule-related attribute tags (Claude),
then body lines with leading horizontal whitespace stripped. Entries separated by a blank line,
then a full-width dash line, then the next header. Same filters as recent_20_simple.sql.

Read-only DB: file:...?mode=ro. Path: CHAT_DB_PATH or ~/Library/Messages/chat.db.
Classification uses ANTHROPIC_API_KEY from project .env (see config.py) unless --no-classify.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import classifier  # noqa: E402
import config  # noqa: E402

# Match reader.APPLE_EPOCH_OFFSET (Apple epoch → Unix seconds)
APPLE_EPOCH_OFFSET = 978307200

RECENT_QUERY = """
SELECT
  m.date,
  m.is_from_me,
  h.id,
  c.chat_identifier,
  m.text
FROM message m
JOIN chat_message_join cmj ON m.rowid = cmj.message_id
JOIN chat c ON cmj.chat_id = c.rowid
LEFT JOIN handle h ON m.handle_id = h.rowid
WHERE m.text IS NOT NULL
  AND m.text != ''
  AND m.associated_message_type = 0
ORDER BY m.date DESC
LIMIT ?;
"""


def expand_db_path(raw: str) -> str:
    p = raw.strip()
    if p.startswith("~"):
        p = str(Path.home()) + p[1:]
    return os.path.expanduser(p)


def fmt_datetime_utc(ns: int | None) -> str:
    if ns is None or ns == 0:
        return "(no datetime)"
    try:
        dt = datetime.utcfromtimestamp(ns / 1e9 + APPLE_EPOCH_OFFSET)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, OverflowError):
        return "(no datetime)"


def fmt_source(is_from_me: int, handle_id: str | None, chat_identifier: str | None) -> str:
    if is_from_me:
        return "me"
    s = (handle_id or chat_identifier or "").strip()
    return s if s else "(no source)"


def terminal_width() -> int:
    """Use current tty width; fall back if not a terminal (e.g. piped)."""
    try:
        return max(20, shutil.get_terminal_size().columns)
    except OSError:
        raw = os.environ.get("COLUMNS", "80")
        try:
            return max(20, int(raw))
        except ValueError:
            return 80


def classify_tags_line(text: str) -> str:
    """Space-separated tags from classifier (same labels rules.py uses)."""
    res = classifier.classify_message(text)
    return " ".join(res.attributes) if res.attributes else "UNKNOWN"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Recent messages: datetime + source + classifier tags, then body (leading WS stripped)."
    )
    parser.add_argument("--limit", type=int, default=20, help="Max messages (default: 20)")
    parser.add_argument(
        "--no-classify",
        action="store_true",
        help="Do not call Claude; print (not classified) on the tags line (no API key needed).",
    )
    args = parser.parse_args()

    if not args.no_classify:
        if not config.ANTHROPIC_API_KEY:
            print(
                "ANTHROPIC_API_KEY is missing from .env — add it or run with --no-classify.",
                file=sys.stderr,
            )
            return 1

    db = expand_db_path(os.environ.get("CHAT_DB_PATH", str(Path.home() / "Library/Messages/chat.db")))
    if not os.path.isfile(db):
        print(f"Cannot read database: {db}", file=sys.stderr)
        print("Grant Full Disk Access (see docs/SETUP.md).", file=sys.stderr)
        return 1

    uri = f"file:{db}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as e:
        print(f"SQLite error: {e}", file=sys.stderr)
        return 1

    try:
        rows = conn.execute(RECENT_QUERY, (args.limit,)).fetchall()
    finally:
        conn.close()

    width = terminal_width()
    for i, (date_ns, is_from_me, handle_id, chat_identifier, text) in enumerate(rows):
        body = text or ""
        if args.no_classify:
            tags_line = "(not classified)"
        else:
            try:
                tags_line = classify_tags_line(body)
            except Exception as e:
                tags_line = f"(classification error: {e})"

        block: list[str] = []
        if i:
            block.append("")
            block.append("-" * width)
        block.append(
            f"{fmt_datetime_utc(date_ns)} {fmt_source(int(is_from_me or 0), handle_id, chat_identifier)}"
        )
        block.append(tags_line)
        for raw_line in body.split("\n"):
            block.append(raw_line.lstrip(" \t"))
        sys.stdout.write("\n".join(block) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
