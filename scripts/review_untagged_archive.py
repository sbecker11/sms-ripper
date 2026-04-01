#!/usr/bin/env python3
"""
List message_tags_archive rows with missing or empty classifier tags for human review.

"Untagged" means: NULL / empty / JSON [] / invalid JSON, and optionally only-UNKNOWN
(see --include-unknown). Use --suggest to run the current classifier on each row and
see how many would get non-empty tags (requires ANTHROPIC_API_KEY in .env).

Read-only on chat.db by default. Quit Messages if the DB is locked.

Usage:
  python scripts/review_untagged_archive.py
  python scripts/review_untagged_archive.py --include-unknown --limit 50
  python scripts/review_untagged_archive.py --suggest --delay 0.2
  python scripts/review_untagged_archive.py --csv --output /tmp/untagged.csv
"""

from __future__ import annotations

import argparse
import csv
import json
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
from reader import apple_ts_to_datetime  # noqa: E402

TABLE = archive.archive_table_name(archive.DEFAULT_ARCHIVE_KEY)
COL = archive.CLASSIFIER_ATTRIBUTES_COLUMN


def _table_for_conn(conn: sqlite3.Connection) -> str:
    return archive.require_archive_table(conn, "education")


def _parse_tags(raw: object) -> list[str] | None:
    if raw is None:
        return []
    s = str(raw).strip()
    if not s:
        return []
    try:
        v = json.loads(s)
    except (TypeError, ValueError):
        return None
    if not isinstance(v, list):
        return None
    return [str(x).strip() for x in v if x is not None and str(x).strip()]


def is_review_candidate(raw: object, *, include_unknown_only: bool) -> bool:
    """True if row should appear in the untagged / review list."""
    tags = _parse_tags(raw)
    if tags is None:
        return True
    if not tags:
        return True
    if include_unknown_only and set(tags) == {"UNKNOWN"}:
        return True
    return False


def _fmt_date_utc(ns: object) -> str:
    if ns is None:
        return ""
    try:
        n = int(ns)
    except (TypeError, ValueError):
        return ""
    dt = apple_ts_to_datetime(n)
    if not dt:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def _table_has_column(conn: sqlite3.Connection, column: str) -> bool:
    # PRAGMA table_info: (cid, name, type, notnull, dflt_value, pk)
    table = _table_for_conn(conn)
    return column in {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def fetch_candidates(
    conn: sqlite3.Connection,
    *,
    include_unknown_only: bool,
    limit: int | None,
) -> list[dict[str, object]]:
    has_classifier = _table_has_column(conn, COL)
    has_handle = _table_has_column(conn, "handle_id")

    sel = f"p.rowid, p.date, p.text, p.{COL}" if has_classifier else f"p.rowid, p.date, p.text, NULL AS {COL}"
    join = ""
    if has_handle:
        join = " LEFT JOIN handle AS h ON p.handle_id = h.rowid"
        sel += ", h.id AS handle"
    else:
        sel += ", NULL AS handle"

    where = ""
    params: list[object] = []
    if has_classifier:
        parts = [
            f"p.{COL} IS NULL",
            f"TRIM(COALESCE(p.{COL}, '')) = ''",
            f"TRIM(COALESCE(p.{COL}, '')) = '[]'",
            f"(p.{COL} IS NOT NULL AND json_valid(p.{COL}) = 0)",
        ]
        if include_unknown_only:
            parts.append(
                f"(json_valid(p.{COL}) = 1 AND "
                f"json_array_length(p.{COL}) = 1 AND "
                f"LOWER(json_extract(p.{COL}, '$[0]')) = 'unknown')"
            )
        where = " WHERE (" + " OR ".join(parts) + ")"

    table = _table_for_conn(conn)
    q = f"SELECT {sel} FROM {table} AS p{join}{where} ORDER BY p.date DESC"
    if limit is not None and limit > 0:
        q += " LIMIT ?"
        params.append(limit)

    rows_out: list[dict[str, object]] = []
    for row in conn.execute(q, tuple(params)).fetchall():
        rowid, date_ns, text, raw_attrs, handle = row
        if has_classifier and not is_review_candidate(
            raw_attrs, include_unknown_only=include_unknown_only
        ):
            continue
        rows_out.append(
            {
                "rowid": rowid,
                "date_ns": date_ns,
                "text": text,
                "stored": raw_attrs,
                "handle": handle,
            }
        )
    return rows_out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="List message_tags_archive rows with empty or missing classifier tags",
    )
    parser.add_argument("--chat-db", type=Path, default=None, help="Override chat.db path")
    parser.add_argument(
        "--include-unknown",
        action="store_true",
        help='Also list rows whose only tag is ["UNKNOWN"]',
    )
    parser.add_argument("--limit", type=int, default=None, metavar="N", help="Max rows to list")
    parser.add_argument(
        "--suggest",
        action="store_true",
        help="Run classifier on each listed row; compare stored vs current model output",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="Seconds between API calls when using --suggest",
    )
    parser.add_argument("--csv", action="store_true", help="Print CSV to stdout")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write CSV here instead of stdout (implies --csv)",
    )
    args = parser.parse_args()

    db_path = Path(args.chat_db or config.CHAT_DB_PATH).expanduser()
    if not db_path.is_file():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 1

    if args.suggest and not config.ANTHROPIC_API_KEY:
        print("ANTHROPIC_API_KEY is missing from .env — required for --suggest.", file=sys.stderr)
        return 1

    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=60.0)
    try:
        table = _table_for_conn(conn)
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if not exists:
            print(f"Table {TABLE} does not exist.", file=sys.stderr)
            return 1

        # Total counts (scan table once for summary)
        def count_review(conn_inner: sqlite3.Connection) -> tuple[int, int]:
            table_inner = _table_for_conn(conn_inner)
            cols = {r[1] for r in conn_inner.execute(f"PRAGMA table_info({table_inner})").fetchall()}
            if COL not in cols:
                n = conn_inner.execute(f"SELECT COUNT(*) FROM {table_inner}").fetchone()[0]
                return n, 0
            nu = 0
            nk = 0
            for (raw,) in conn_inner.execute(f"SELECT {COL} FROM {table_inner}").fetchall():
                if is_review_candidate(raw, include_unknown_only=False):
                    nu += 1
                elif is_review_candidate(raw, include_unknown_only=True) and not is_review_candidate(
                    raw, include_unknown_only=False
                ):
                    nk += 1
            return nu, nk

        strict_total, unknown_extra_total = count_review(conn)
        has_classifier_col = _table_has_column(conn, COL)

        rows = fetch_candidates(
            conn,
            include_unknown_only=args.include_unknown,
            limit=args.limit,
        )
    finally:
        conn.close()

    csv_mode = args.csv or args.output is not None
    out_file = open(args.output, "w", encoding="utf-8", newline="") if args.output else None
    try:
        writer = None
        if csv_mode:
            stream = out_file if out_file else sys.stdout
            fieldnames = [
                "rowid",
                "date_utc",
                "handle",
                "stored_classifier_attributes",
                "text_preview",
            ]
            if args.suggest:
                fieldnames.extend(["suggested_attributes", "suggested_reason", "tags_changed"])
            writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()

        if not csv_mode:
            print(f"{TABLE} — rows with empty/missing tags (strict): {strict_total}")
            print(
                f"['UNKNOWN']-only rows (count; use --include-unknown to include in list): {unknown_extra_total}"
            )
            if not has_classifier_col:
                print(f"(no {COL} column — every row is untagged; use --limit)")
            print(f"Listed below: {len(rows)} row(s)" + (f" (limit {args.limit})" if args.limit else ""))
            print()

        suggest_changed = 0
        suggest_errors = 0
        delay = max(0.0, args.delay)

        for i, r in enumerate(rows):
            if args.suggest and delay and i > 0:
                time.sleep(delay)
            rowid = r["rowid"]
            text = "" if r["text"] is None else str(r["text"])
            preview = text.replace("\n", " ")[:500]
            stored = r["stored"]
            handle = r["handle"] or ""
            date_s = _fmt_date_utc(r["date_ns"])

            suggested: list[str] | None = None
            reason = ""
            changed = ""
            if args.suggest:
                try:
                    res = classifier.classify_message(text)
                    suggested = res.attributes
                    reason = res.reason
                    st_tags = _parse_tags(stored)
                    if st_tags is None:
                        st_tags = []
                    changed = "yes" if set(suggested) != set(st_tags) else "no"
                    if changed == "yes":
                        suggest_changed += 1
                except Exception as e:  # noqa: BLE001
                    suggested = None
                    reason = f"error: {e}"
                    changed = "error"
                    suggest_errors += 1

            if writer:
                row_out = {
                    "rowid": rowid,
                    "date_utc": date_s,
                    "handle": handle,
                    "stored_classifier_attributes": stored if stored is not None else "",
                    "text_preview": preview,
                }
                if args.suggest:
                    row_out["suggested_attributes"] = json.dumps(suggested) if suggested else ""
                    row_out["suggested_reason"] = reason[:500] if reason else ""
                    row_out["tags_changed"] = changed
                writer.writerow(row_out)
            else:
                print(f"--- rowid={rowid}  {date_s}  handle={handle}")
                print(f"    stored: {stored!r}")
                print(f"    text:   {preview[:240]}{'…' if len(preview) > 240 else ''}")
                if args.suggest and suggested is not None:
                    print(f"    suggest: {suggested}  (changed vs stored: {changed})")
                    if reason and not reason.startswith("error:"):
                        print(f"    reason: {reason[:200]}{'…' if len(reason) > 200 else ''}")
                elif args.suggest:
                    print(f"    suggest: FAILED {reason}")
                print()

        if not csv_mode and args.suggest and rows:
            print(
                f"Summary: suggested tags differ from stored on {suggest_changed} / {len(rows)} row(s); "
                f"errors: {suggest_errors}"
            )
    finally:
        if out_file:
            out_file.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
