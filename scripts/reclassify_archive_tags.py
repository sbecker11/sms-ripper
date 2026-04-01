#!/usr/bin/env python3
"""
Re-run the classifier on rows in message_tags_archive and refresh classifier_attributes (JSON).

Stores ``{"attributes": [...], "weights": {"TAG": 0.0–1.0, ...}}`` (legacy list rows are upgraded
on write). Uses the same classifier + ``education`` keyword merge heuristics as live processing. Requires
ANTHROPIC_API_KEY in the project .env (see config).

Writes only to message_tags_archive.classifier_attributes — not to the live message table.
Quit Messages.app before running if chat.db writes are otherwise locked.

Usage:
  python scripts/reclassify_archive_tags.py --dry-run
  python scripts/reclassify_archive_tags.py --limit 50
  python scripts/reclassify_archive_tags.py --delay 0.25
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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

TABLE = archive.archive_table_name(archive.DEFAULT_ARCHIVE_KEY)
COL = archive.CLASSIFIER_ATTRIBUTES_COLUMN


def _validate_table_name(name: str) -> str:
    t = str(name or "").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", t):
        raise ValueError(f"Unsafe table name: {name!r}")
    return t


def _existing_archive_tables(conn: sqlite3.Connection) -> list[str]:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE ? ORDER BY name",
        ("%_archive",),
    )
    return [str(r[0]) for r in cur.fetchall() if str(r[0]).strip()]


def reclassify_archive_tags(
    conn: sqlite3.Connection,
    *,
    table: str = TABLE,
    dry_run: bool,
    limit: int | None,
    delay_sec: float,
    show_progress: bool = True,
    workers: int = 1,
) -> tuple[int, int, int]:
    """
    Returns (updated_count, unchanged_count, error_count).
    """
    table = _validate_table_name(table)
    if table == TABLE:
        table = archive.require_archive_table(conn, "education")
    else:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone():
            raise RuntimeError(f"Archive table {table!r} does not exist in this database.")
    archive._ensure_classifier_attributes_column(conn, table)
    q = f"SELECT rowid, text, {COL} FROM {table} ORDER BY rowid DESC"
    params: tuple = ()
    if limit is not None and limit > 0:
        q += " LIMIT ?"
        params = (limit,)
    cur = conn.execute(q, params)
    rows = cur.fetchall()
    total = len(rows)

    updated = 0
    unchanged = 0
    errors = 0

    # Keep output readable on large archives while still giving completion visibility.
    progress_step = 1 if total <= 25 else 10
    workers_n = max(1, int(workers))

    def _emit_progress(done_count: int) -> None:
        if show_progress and (done_count == 1 or done_count % progress_step == 0 or done_count == total):
            pct = (100.0 * done_count / total) if total else 100.0
            print(
                f"[progress] {done_count}/{total} ({pct:5.1f}%) "
                f"updated={updated} unchanged={unchanged} errors={errors}"
            )

    def _classify_one(item: tuple[int, object, object]) -> tuple[int, object, object, object | None, Exception | None]:
        rowid_i, text_i, old_raw_i = item
        body = "" if text_i is None else str(text_i)
        try:
            res_i = classifier.classify_message(body)
            return rowid_i, old_raw_i, body, res_i, None
        except Exception as e:  # noqa: BLE001 — propagate per-row errors
            return rowid_i, old_raw_i, body, None, e

    if workers_n == 1:
        done = 0
        for rowid, text, old_raw in rows:
            _rid, _old_raw, _body, res, err = _classify_one((rowid, text, old_raw))
            done += 1
            if err is not None:
                print(f"[rowid {rowid}] classify error: {err}", file=sys.stderr)
                errors += 1
                _emit_progress(done)
                continue
            assert res is not None
            new_json = classifier.encode_classifier_blob(res.attributes, res.weights)
            old_attrs, _old_w = classifier.decode_classifier_blob(old_raw)
            if old_attrs == res.attributes:
                unchanged += 1
                _emit_progress(done)
                continue
            if dry_run:
                print(f"[dry-run] rowid={rowid} old={old_raw!r} new={new_json}")
                updated += 1
                _emit_progress(done)
                continue
            conn.execute(
                f'UPDATE {table} SET {COL} = ? WHERE rowid = ?',
                (new_json, rowid),
            )
            updated += 1
            _emit_progress(done)
            if delay_sec > 0:
                time.sleep(delay_sec)
    else:
        # Parallel classification, serialized DB writes.
        done = 0
        with ThreadPoolExecutor(max_workers=workers_n) as ex:
            fut_map = {ex.submit(_classify_one, row): row[0] for row in rows}
            for fut in as_completed(fut_map):
                rowid = fut_map[fut]
                done += 1
                try:
                    _rid, old_raw, _body, res, err = fut.result()
                except Exception as e:  # noqa: BLE001
                    print(f"[rowid {rowid}] classify error: {e}", file=sys.stderr)
                    errors += 1
                    _emit_progress(done)
                    continue
                if err is not None:
                    print(f"[rowid {rowid}] classify error: {err}", file=sys.stderr)
                    errors += 1
                    _emit_progress(done)
                    continue
                assert res is not None
                new_json = classifier.encode_classifier_blob(res.attributes, res.weights)
                old_attrs, _old_w = classifier.decode_classifier_blob(old_raw)
                if old_attrs == res.attributes:
                    unchanged += 1
                    _emit_progress(done)
                    continue
                if dry_run:
                    print(f"[dry-run] rowid={rowid} old={old_raw!r} new={new_json}")
                    updated += 1
                    _emit_progress(done)
                    continue
                conn.execute(
                    f'UPDATE {table} SET {COL} = ? WHERE rowid = ?',
                    (new_json, rowid),
                )
                updated += 1
                _emit_progress(done)
                if delay_sec > 0:
                    time.sleep(delay_sec)

    if not dry_run:
        conn.commit()
    return updated, unchanged, errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reclassify message_tags_archive rows and update classifier_attributes",
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
    parser.add_argument(
        "--workers",
        type=int,
        default=max(1, int(getattr(config, "CLASSIFY_MAX_WORKERS", 8))),
        metavar="N",
        help="Parallel classifier workers (default CLASSIFY_MAX_WORKERS from .env/config)",
    )
    parser.add_argument(
        "--table",
        default=TABLE,
        metavar="NAME",
        help=f"Archive table to process (default: {TABLE})",
    )
    parser.add_argument(
        "--tables",
        default=None,
        metavar="CSV",
        help="Comma-separated archive tables to process (overrides --table)",
    )
    parser.add_argument(
        "--all-archives",
        action="store_true",
        help="Process every existing *_archive table (overrides --table/--tables)",
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
        if args.all_archives:
            tables = _existing_archive_tables(conn)
        elif args.tables:
            tables = [_validate_table_name(p) for p in str(args.tables).split(",") if p.strip()]
        else:
            tables = [_validate_table_name(args.table)]
        if not tables:
            print("No archive tables selected.", file=sys.stderr)
            return 1

        selected: list[str] = []
        for t in tables:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (t,),
            ).fetchone()
            if not exists:
                print(f"Table {t} does not exist; skipping.", file=sys.stderr)
                continue
            selected.append(t)
        if not selected:
            print("No selected archive tables exist.", file=sys.stderr)
            return 1

        u_total = 0
        same_total = 0
        err_total = 0
        for t in selected:
            print(f"[start] table={t}")
            u, same, err = reclassify_archive_tags(
                conn,
                table=t,
                dry_run=args.dry_run,
                limit=args.limit,
                delay_sec=max(0.0, args.delay),
                workers=max(1, int(args.workers)),
            )
            u_total += u
            same_total += same
            err_total += err
            print(
                f"[table] {t} updated={u} unchanged={same} errors={err}"
            )
    finally:
        conn.close()

    mode = "dry-run" if args.dry_run else "done"
    print(
        f"[{mode}] updated={u_total} unchanged={same_total} errors={err_total} "
        f"(tables={','.join(selected)})"
    )
    return 0 if err_total == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
