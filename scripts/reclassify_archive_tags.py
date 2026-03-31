#!/usr/bin/env python3
"""
Re-run the classifier on rows in POLITICAL_archive and refresh classifier_attributes (JSON).

Stores ``{"attributes": [...], "weights": {"TAG": 0.0–1.0, ...}}`` (legacy list rows are upgraded
on write). Uses the same classifier + POLITICAL merge heuristics as live processing. Requires
ANTHROPIC_API_KEY in the project .env (see config).

Writes only to POLITICAL_archive.classifier_attributes — not to the live message table.
Quit Messages.app before running if chat.db writes are otherwise locked.

Usage:
  python scripts/reclassify_archive_tags.py --dry-run
  python scripts/reclassify_archive_tags.py --limit 50
  python scripts/reclassify_archive_tags.py --delay 0.25
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SCRIPTS = Path(__file__).resolve().parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import config  # noqa: E402
import archive  # noqa: E402
import classifier  # noqa: E402

TABLE = "POLITICAL_archive"
COL = archive.CLASSIFIER_ATTRIBUTES_COLUMN


def reclassify_archive_tags(
    conn: sqlite3.Connection,
    *,
    dry_run: bool,
    limit: int | None,
    delay_sec: float,
) -> tuple[int, int, int]:
    """
    Returns (updated_count, unchanged_count, error_count).
    """
    archive._ensure_classifier_attributes_column(conn, TABLE)
    q = f"SELECT rowid, text, {COL} FROM {TABLE} ORDER BY rowid DESC"
    params: tuple = ()
    if limit is not None and limit > 0:
        q += " LIMIT ?"
        params = (limit,)
    cur = conn.execute(q, params)
    rows = cur.fetchall()

    updated = 0
    unchanged = 0
    errors = 0

    for rowid, text, old_raw in rows:
        body = "" if text is None else str(text)
        try:
            res = classifier.classify_message(body)
        except Exception as e:  # noqa: BLE001 — log and continue
            print(f"[rowid {rowid}] classify error: {e}", file=sys.stderr)
            errors += 1
            continue

        new_json = classifier.encode_classifier_blob(res.attributes, res.weights)
        old_attrs, _old_w = classifier.decode_classifier_blob(old_raw)
        if old_attrs == res.attributes:
            unchanged += 1
            continue

        if dry_run:
            print(
                f"[dry-run] rowid={rowid} old={old_raw!r} new={new_json}",
            )
            updated += 1
            continue

        conn.execute(
            f'UPDATE {TABLE} SET {COL} = ? WHERE rowid = ?',
            (new_json, rowid),
        )
        updated += 1
        if delay_sec > 0:
            time.sleep(delay_sec)

    if not dry_run:
        conn.commit()
    return updated, unchanged, errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reclassify POLITICAL_archive rows and update classifier_attributes",
    )
    parser.add_argument(
        "--chat-db",
        type=Path,
        default=None,
        help="Override chat.db path (default: CHAT_DB_PATH from .env)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print changes that would be written; do not write",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Process at most N rows (newest first by rowid desc)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        metavar="SEC",
        help="Sleep between API calls (rate limiting; default 0)",
    )
    args = parser.parse_args()

    db_path = Path(args.chat_db or config.CHAT_DB_PATH).expanduser()
    if not db_path.is_file():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 1

    if not config.ANTHROPIC_API_KEY:
        print(
            "ANTHROPIC_API_KEY is missing from .env — required for classification.",
            file=sys.stderr,
        )
        return 1

    uri = f"file:{db_path}?mode=rwc"
    conn = sqlite3.connect(uri, uri=True, timeout=60.0)
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (TABLE,),
        ).fetchone()
        if not exists:
            print(f"Table {TABLE} does not exist.", file=sys.stderr)
            return 1

        u, same, err = reclassify_archive_tags(
            conn,
            dry_run=args.dry_run,
            limit=args.limit,
            delay_sec=max(0.0, args.delay),
        )
    finally:
        conn.close()

    mode = "dry-run" if args.dry_run else "done"
    print(
        f"[{mode}] updated={u} unchanged={same} errors={err} "
        f"(table={TABLE})"
    )
    return 0 if err == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
