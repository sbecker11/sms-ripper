#!/usr/bin/env python3
"""
Write a self-contained static HTML report of POLITICAL_archive (and summary stats).

Designed to run after each daemon cycle; open the file in a browser or bookmark a file:// URL.
Optional meta-refresh reloads the page periodically while it stays open (picks up new file content).

Usage:
  python scripts/generate_report_html.py
  python scripts/generate_report_html.py --output reports/index.html
"""

from __future__ import annotations

import argparse
import html
import sqlite3
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import config  # noqa: E402
from reader import apple_ts_to_datetime  # noqa: E402

DEFAULT_OUTPUT = _REPO / "reports" / "index.html"
TABLE = "POLITICAL_archive"
META_REFRESH_SEC = 900  # align with default daemon interval; 0 = disable


def _fmt_ts(ns: int | None) -> str:
    if ns is None:
        return "—"
    dt = apple_ts_to_datetime(int(ns))
    if not dt:
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _truncate(s: str | None, n: int = 240) -> str:
    if not s:
        return "—"
    t = " ".join(s.split())
    if len(t) <= n:
        return t
    return t[: n - 1] + "…"


def _build_html(
    *,
    generated_at: str,
    total_archived: int,
    rows: list[dict[str, object]],
    db_path: str,
    error: str | None,
) -> str:
    refresh_tag = ""
    if META_REFRESH_SEC > 0:
        refresh_tag = (
            f'<meta http-equiv="refresh" content="{META_REFRESH_SEC}" />\n'
            f"<!-- Reloads every {META_REFRESH_SEC}s while open; file is rewritten each daemon run -->\n"
        )

    if error:
        body = f"<p class=\"err\">{html.escape(error)}</p>"
        rows_html = '<tr><td colspan="4" class="err">Could not load archive rows.</td></tr>'
    else:
        body = (
            f"<p><strong>{total_archived}</strong> row(s) in <code>{html.escape(TABLE)}</code> · "
            f"database <code>{html.escape(db_path)}</code></p>"
        )
        parts = []
        for r in rows:
            parts.append(
                "<tr>"
                f"<td>{html.escape(str(r.get('rowid', '')))}</td>"
                f"<td>{html.escape(_fmt_ts(r.get('date') if r.get('date') is not None else None))}</td>"
                f"<td class=\"mono\">{html.escape(_truncate(str(r.get('handle') or '')))}</td>"
                f"<td>{html.escape(_truncate(str(r.get('text') or '')))}</td>"
                "</tr>"
            )
        rows_html = "\n".join(parts) if parts else "<tr><td colspan=\"4\">(no rows)</td></tr>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  {refresh_tag}  <title>sms-ripper — political archive report</title>
  <style>
    :root {{ font-family: system-ui, sans-serif; background: #111; color: #e8e8e8; }}
    body {{ max-width: 1100px; margin: 2rem auto; padding: 0 1rem; }}
    h1 {{ font-size: 1.25rem; }}
    .meta {{ color: #888; font-size: 0.9rem; margin-bottom: 1.5rem; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
    th, td {{ border: 1px solid #333; padding: 0.5rem 0.6rem; text-align: left; vertical-align: top; }}
    th {{ background: #1e1e1e; }}
    tr:nth-child(even) {{ background: #161616; }}
    .mono {{ font-family: ui-monospace, monospace; max-width: 12rem; word-break: break-all; }}
    .hint {{ background: #1a1a2e; border: 1px solid #334; padding: 0.75rem 1rem; margin-top: 1.5rem; font-size: 0.85rem; }}
    .err {{ color: #f66; }}
    code {{ font-size: 0.9em; }}
  </style>
</head>
<body>
  <h1>Political archive report</h1>
  <p class="meta">Generated {html.escape(generated_at)} · updates when <code>generate_report_html.py</code> runs (e.g. end of each daemon cycle)</p>
  {body}
  <h2>Latest archived messages (newest first)</h2>
  <table>
    <thead><tr><th>rowid</th><th>date</th><th>handle</th><th>text (truncated)</th></tr></thead>
    <tbody>
{rows_html}
    </tbody>
  </table>
  <div class="hint">
    Open via Finder double-click or <code>open …/reports/index.html</code> (adjust if you used <code>--output</code>), then bookmark the <code>file://</code> URL.
    This page is self-contained; no web server is needed. Meta-refresh reloads the file from disk while the tab stays open; each daemon run overwrites it.
  </div>
</body>
</html>
"""


def _fetch_rows(conn: sqlite3.Connection, limit: int) -> tuple[int, list[dict[str, object]]]:
    cur = conn.execute(
        f"SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (TABLE,),
    )
    if not cur.fetchone():
        return 0, []

    total = int(conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0])
    # Join handle when both tables exist
    q = f"""
    SELECT p.rowid AS rowid, p.date AS date, p.text AS text, h.id AS handle
    FROM {TABLE} AS p
    LEFT JOIN handle AS h ON p.handle_id = h.rowid
    ORDER BY p.date DESC
    LIMIT ?
    """
    try:
        cur = conn.execute(q, (limit,))
    except sqlite3.Error:
        q2 = f"SELECT rowid, date, text, NULL AS handle FROM {TABLE} ORDER BY date DESC LIMIT ?"
        cur = conn.execute(q2, (limit,))
    out: list[dict[str, object]] = []
    for row in cur.fetchall():
        out.append(
            {
                "rowid": row[0],
                "date": row[1],
                "text": row[2],
                "handle": row[3],
            }
        )
    return total, out


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate static HTML report for POLITICAL_archive")
    parser.add_argument("--chat-db", type=Path, default=None, help="Override chat.db path")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output HTML path")
    parser.add_argument("--limit", type=int, default=100, help="Max rows in table")
    args = parser.parse_args()

    db_path = Path(args.chat_db or config.CHAT_DB_PATH).expanduser()
    out_path = Path(args.output).expanduser()
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    error: str | None = None
    total = 0
    rows: list[dict[str, object]] = []

    try:
        if not db_path.is_file():
            error = f"Database not found: {db_path}"
        else:
            uri = f"file:{db_path}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=30.0)
            try:
                total, rows = _fetch_rows(conn, max(1, args.limit))
            finally:
                conn.close()
    except Exception as e:
        error = f"{e}\n{traceback.format_exc()}"

    html_doc = _build_html(
        generated_at=generated_at,
        total_archived=total,
        rows=rows,
        db_path=str(db_path),
        error=error,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_doc, encoding="utf-8")
    print(f"Wrote {out_path}")
    if error:
        print(error, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
