#!/usr/bin/env python3
"""
Dry-run preview: recent messages from chat.db (read-only), classifier tags, matched rules,
planned actions (rule merge vs execution order — same as actions.execute_actions), and SQLite steps.
No API calls with --no-classify.

Same message filter/order as scripts/format_recent_simple.py (all directions, by date desc).
Each message is written to stdout in one shot after classification and rule evaluation complete.
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

import actions  # noqa: E402
import classifier  # noqa: E402
import config  # noqa: E402
import rules  # noqa: E402
from reader import Message, apple_ts_to_datetime  # noqa: E402

APPLE_EPOCH_OFFSET = 978307200

# Actions in this project that perform direct SQLite writes on chat.db (see archive.py).
SQLITE_DB_ACTIONS: frozenset[str] = frozenset({"archive"})

RECENT_QUERY = """
SELECT
  m.rowid,
  c.rowid AS chat_id,
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
    try:
        return max(20, shutil.get_terminal_size().columns)
    except OSError:
        raw = os.environ.get("COLUMNS", "80")
        try:
            return max(20, int(raw))
        except ValueError:
            return 80


def row_to_message(row: tuple) -> Message:
    rowid, chat_id, date_ns, is_from_me, handle_id, chat_identifier, text = row
    return Message(
        rowid=int(rowid),
        chat_id=int(chat_id),
        chat_identifier=(chat_identifier or ""),
        sender=handle_id,
        text=text or "",
        is_from_me=bool(is_from_me),
        date=apple_ts_to_datetime(int(date_ns) if date_ns else None),
        attributes=[],
    )


def classify_and_evaluate(msg: Message, *, no_classify: bool) -> tuple[list[str], list[str], list[str]]:
    """Set msg.attributes; return (attributes, matched_rule_names, actions)."""
    if no_classify:
        msg.attributes = ["UNKNOWN"]
    else:
        try:
            attrs, _reason = classifier.classify_message(msg.text)
            msg.attributes = attrs
        except Exception:
            msg.attributes = ["UNKNOWN"]
    action_list, matched_names = rules.evaluate_detailed(msg)
    return msg.attributes, matched_names, action_list


def action_lines(action_list: list[str]) -> list[str]:
    """Rule-merge vs execution order (matches actions._execution_action_order)."""
    ordered = actions._execution_action_order(action_list)
    out: list[str] = []
    if action_list != ordered:
        out.append(f"Actions (rule merge): {action_list}")
    out.append(f"Actions (execution): {ordered}")
    return out


def flush_block(lines: list[str]) -> None:
    """Write one message block after all lines are assembled."""
    sys.stdout.write("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dry-run: recent messages, tags, matched rules, actions, SQLite-affecting steps."
    )
    parser.add_argument("--limit", type=int, default=20, help="Max messages (default: 20)")
    parser.add_argument(
        "--no-classify",
        action="store_true",
        help="Skip Claude; use attributes [UNKNOWN] for rule preview only.",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Only print Attributes / Matched rules / Actions per message (plus one header line each).",
    )
    args = parser.parse_args()

    if not args.no_classify and not config.ANTHROPIC_API_KEY:
        print(
            "ANTHROPIC_API_KEY is missing from .env — add it or use --no-classify.",
            file=sys.stderr,
        )
        return 1

    db = expand_db_path(os.environ.get("CHAT_DB_PATH", str(Path.home() / "Library/Messages/chat.db")))
    if not os.path.isfile(db):
        print(f"Cannot read database: {db}", file=sys.stderr)
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

    total = len(rows)
    width = terminal_width()
    if not args.compact:
        print(
            "Dry-run preview (no actions executed). "
            "SQLite = direct chat.db writes; other actions use AppleScript or local files.\n"
        )

    for i, row in enumerate(rows):
        msg = row_to_message(row)
        date_ns = row[2]
        is_from_me = int(row[3] or 0)
        handle_id = row[4]
        chat_identifier = row[5]
        body = msg.text
        ts = fmt_datetime_utc(int(date_ns) if date_ns else None)
        who = fmt_source(is_from_me, handle_id, chat_identifier)

        block: list[str] = []

        if args.compact:
            preview = (body or "").replace("\n", " ").strip()
            if len(preview) > 100:
                preview = preview[:97] + "..."
            attrs, matched_names, action_list = classify_and_evaluate(
                msg, no_classify=args.no_classify
            )
            names_s = ", ".join(matched_names) if matched_names else "(none — default log_only)"
            if i:
                block.append("")
            block.extend(
                [
                    f"--- {i + 1}/{total} | rowid={msg.rowid} | {ts} | {who} | {preview!r}",
                    f"Attributes: {attrs}",
                    f"Matched rules: {names_s}",
                    *action_lines(action_list),
                ]
            )
            flush_block(block)
            continue

        if args.no_classify:
            msg.attributes = ["UNKNOWN"]
            tag_lines = ["Tags: UNKNOWN  (not classified — rules use [UNKNOWN])"]
        else:
            try:
                attrs, reason = classifier.classify_message(msg.text)
                msg.attributes = attrs
                rshort = (reason or "").replace("\n", " ").strip()
                if len(rshort) > 120:
                    rshort = rshort[:117] + "..."
                tag_lines = [f"Tags: {' '.join(attrs)}", f"Classifier: {rshort}"]
            except Exception as e:
                msg.attributes = ["UNKNOWN"]
                tag_lines = [f"Tags: (classification error: {e})"]

        action_list, matched_names = rules.evaluate_detailed(msg)
        names_s = ", ".join(matched_names) if matched_names else "(none — default log_only)"
        ordered = actions._execution_action_order(action_list)
        db_part = [a for a in ordered if a in SQLITE_DB_ACTIONS]
        sqlite_line = (
            "chat.db (SQLite): "
            + (", ".join(db_part) if db_part else "(none for this message)")
        )

        if i:
            block.append("")
            block.append("-" * width)
        block.append(f"{i + 1}/{total} {ts} {who}")
        block.append(f"rowid={msg.rowid} chat_id={msg.chat_id} chat={msg.chat_identifier!r}")
        block.extend(tag_lines)
        block.append(f"Matched rules: {names_s}")
        block.extend(action_lines(action_list))
        block.append(sqlite_line)
        block.append("")
        for raw_line in body.split("\n"):
            block.append(raw_line.lstrip(" \t"))
        flush_block(block)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
