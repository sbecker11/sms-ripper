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
import json
import re
import sqlite3
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SCRIPTS = Path(__file__).resolve().parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import config  # noqa: E402
import html_tz_toggle  # noqa: E402
from reader import apple_ts_to_datetime  # noqa: E402

DEFAULT_OUTPUT = _REPO / "reports" / "index.html"
TABLE = "POLITICAL_archive"
META_REFRESH_SEC = 900  # align with default daemon interval; 0 = disable

# Inline SVG (chain link) — self-contained for file://; no extension needed.
_SVG_CYCLE_LINK = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M4.715 6.542 3.343 7.914a3 3 0 1 0 4.243 4.243l1.828-1.829A3 3 0 0 0 8.586 5.5L8 6.086a1 1 0 0 0-.029 1.415l1.415 1.415a1 1 0 0 0 1.415-.029l1.829-1.828a3 3 0 1 0-4.243-4.243L6.343 4.542A3 3 0 0 0 4.5 6.386l.215.156zm7.794-2.894a1 1 0 0 0-1.414 1.414l1.415 1.415a1 1 0 0 0 1.414-1.414l-1.415-1.415z"/></svg>"""

# File + pop-out — open full body in a new window (Bootstrap-style file-earmark + corner).
_SVG_FULL_TEXT = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M14 4.5V14a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V2a2 2 0 0 1 2-2h5.5L14 4.5zM9.5 3V2H4a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1V5h-4.5A1.5 1.5 0 0 1 9.5 3zM5 6.5a.5.5 0 0 1 .5-.5h4a.5.5 0 0 1 0 1h-4a.5.5 0 0 1-.5-.5zm0 2a.5.5 0 0 1 .5-.5h4a.5.5 0 0 1 0 1h-4a.5.5 0 0 1-.5-.5zm0 2a.5.5 0 0 1 .5-.5h2a.5.5 0 0 1 0 1h-2a.5.5 0 0 1-.5-.5zM1 4h1v8H1V4zm2 3.5L5.5 10 8 7.5H6V5H4v2.5z"/></svg>"""


def _fmt_apple_date_cell_html(ns: object | None) -> str:
    """Single instant: default UTC text + data-utc for browser Time toggle."""
    if ns is None:
        return "—"
    try:
        n = int(ns)
    except (TypeError, ValueError):
        return "—"
    dt_naive = apple_ts_to_datetime(n)
    if not dt_naive:
        return "—"
    dt_utc = dt_naive.replace(tzinfo=timezone.utc)
    iso = dt_utc.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    utc_d = dt_utc.strftime("%Y-%m-%d")
    utc_t = dt_utc.strftime("%H:%M:%S UTC")
    return (
        f'<span class="dt-adjustable" data-utc="{html.escape(iso)}">'
        f"{html.escape(utc_d)}<br/>{html.escape(utc_t)}</span>"
    )


def _cycle_report_href(start: object, pid: object) -> str:
    slug = f"{str(start).replace(':', '-')}_{pid}"
    return f"daemon-cycles/cycle_{slug}.html"


def _cycle_cell(r: dict[str, object]) -> str:
    start = r.get("daemon_cycle_start")
    pid = r.get("daemon_cycle_pid")
    if start is None or pid is None:
        return "—"
    ss, pp = str(start).strip(), str(pid).strip()
    if not ss or not pp:
        return "—"
    href = _cycle_report_href(ss, pp)
    label = f"Daemon cycle {ss} (pid {pp})"
    return (
        f"<a class=\"icon-nav\" href=\"{html.escape(href)}\" "
        f"title=\"{html.escape(ss)}\" aria-label=\"{html.escape(label)}\">{_SVG_CYCLE_LINK}</a>"
    )


def _full_text_open_button(rowid: object) -> str:
    try:
        rid_int = int(rowid)
    except (TypeError, ValueError):
        return "—"
    label = f"Open full message text (rowid {rid_int}) in a new window"
    return (
        f'<button type="button" class="icon-nav text-full-open" '
        f'onclick="smsRipperOpenArchiveFull({rid_int})" '
        f'title="{html.escape(label)}" aria-label="{html.escape(label)}">'
        f"{_SVG_FULL_TEXT}</button>"
    )


def _truncate(s: str | None, n: int = 240) -> str:
    if not s:
        return "—"
    t = " ".join(s.split())
    if len(t) <= n:
        return t
    return t[: n - 1] + "…"


def _build_html(
    *,
    generated_at_iso: str,
    generated_at_date: str,
    generated_at_time: str,
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

    full_text_json = json.dumps(
        {
            str(r.get("rowid")): ("" if r.get("text") is None else str(r.get("text")))
            for r in rows
            if r.get("rowid") is not None
        },
        ensure_ascii=False,
    )
    # HTML parsers treat </script inside inline script as closing the tag even inside JSON strings.
    full_text_json = re.sub(r"</(?=script)", r"<\\/", full_text_json, flags=re.IGNORECASE)
    full_text_script = f"""<script>
(function () {{
  window.SMS_RIPPER_ARCHIVE_FULL = {full_text_json};
  window.smsRipperOpenArchiveFull = function (rowid) {{
    var map = window.SMS_RIPPER_ARCHIVE_FULL || {{}};
    var t = map[String(rowid)];
    if (t === undefined || t === null) t = "";
    var w = window.open("", "_blank", "noopener,noreferrer");
    if (!w) return;
    var doc = w.document;
    doc.open();
    doc.write("<!DOCTYPE html><html lang=\\"en\\"><head><meta charset=\\"utf-8\\"><meta name=\\"viewport\\" content=\\"width=device-width, initial-scale=1\\"><title>Archived message " + String(rowid) + "</title><style>body{{margin:0;background:#111;color:#e8e8e8;font-family:system-ui,sans-serif;padding:1rem 1.25rem;line-height:1.5;white-space:pre-wrap;word-break:break-word;font-size:0.95rem;}}</style></head><body></body></html>");
    doc.close();
    doc.body.textContent = t;
  }};
}})();
</script>
"""

    if error:
        body = f"<p class=\"err\">{html.escape(error)}</p>"
        rows_html = '<tr><td colspan="6" class="err">Could not load archive rows.</td></tr>'
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
                f"<td class=\"ts-dual col-datetime\">{_fmt_apple_date_cell_html(r.get('date'))}</td>"
                f"<td class=\"mono\">{html.escape(_truncate(str(r.get('handle') or '')))}</td>"
                f"<td class=\"cycle-link\">{_cycle_cell(r)}</td>"
                f"<td>{html.escape(_truncate(str(r.get('text') or '')))}</td>"
                f"<td class=\"text-full-col\">{_full_text_open_button(r.get('rowid'))}</td>"
                "</tr>"
            )
        rows_html = "\n".join(parts) if parts else "<tr><td colspan=\"6\">(no rows)</td></tr>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  {refresh_tag}  <title>sms-ripper — political archive report</title>
  <style>
    {html_tz_toggle.THEME_CSS}
    :root {{ font-family: system-ui, sans-serif; }}
    html {{ background: var(--sr-bg-page); color: var(--sr-fg); }}
    body {{ max-width: 1100px; margin: 2rem auto; padding: 0 1rem; }}
    a {{ color: var(--sr-link); }}
    a:visited {{ color: var(--sr-link-visited); }}
    a:hover {{ color: var(--sr-link-hover); }}
    h1 {{ font-size: 1.25rem; }}
    .meta {{ color: var(--sr-fg-muted); font-size: 0.9rem; margin-bottom: 1.5rem; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
    th, td {{ border: 1px solid var(--sr-border); padding: 0.5rem 0.6rem; text-align: left; vertical-align: top; }}
    th {{ background: var(--sr-th-bg); }}
    tr:nth-child(even) {{ background: var(--sr-tr-alt); }}
    .mono {{ font-family: ui-monospace, monospace; max-width: 12rem; word-break: break-all; }}
    td.ts-dual {{ font-size: 0.82rem; line-height: 1.35; vertical-align: top; word-break: break-word; }}
    .cycle-link {{ text-align: center; width: 2.75rem; vertical-align: middle; }}
    a.icon-nav, button.icon-nav {{ color: var(--sr-link); text-decoration: none; display: inline-flex; align-items: center; justify-content: center; }}
    button.icon-nav {{ border: none; background: transparent; padding: 0; cursor: pointer; font: inherit; }}
    a.icon-nav:hover, button.icon-nav:hover {{ color: var(--sr-link-hover); }}
    a.icon-nav svg, button.icon-nav svg {{ display: block; }}
    .text-full-col {{ text-align: center; width: 2.75rem; vertical-align: middle; }}
    .hint {{ background: var(--sr-hint-bg); border: 1px solid var(--sr-hint-border); padding: 0.75rem 1rem; margin-top: 1.5rem; font-size: 0.85rem; }}
    .err {{ color: var(--sr-err); }}
    code {{ font-size: 0.9em; }}
    {html_tz_toggle.TOGGLE_CSS}
  </style>
{html_tz_toggle.THEME_BOOTSTRAP_HEAD}
</head>
<body>
{html_tz_toggle.TOGGLE_HTML}
  <h1>Political archive report</h1>
  <p class="meta">Generated <span class="dt-adjustable" data-utc="{html.escape(generated_at_iso)}">{html.escape(generated_at_date)}<br/>{html.escape(generated_at_time)}</span> · updates when <code>generate_report_html.py</code> runs (e.g. end of each daemon cycle)</p>
  {body}
  <h2>Latest archived messages (newest first)</h2>
  <table>
    <thead><tr><th>rowid</th><th class="col-datetime" title="Message instant in UTC; use UTC / Local buttons for your browser">date</th><th>handle</th><th title="Daemon cycle log (same slug as reports/daemon-cycles/)">cycle</th><th>text (truncated)</th><th title="Open full message body in a new window">full</th></tr></thead>
    <tbody>
{rows_html}
    </tbody>
  </table>
  <div class="hint">
    Open via Finder double-click or <code>open …/reports/index.html</code> (adjust if you used <code>--output</code>), then bookmark the <code>file://</code> URL.
    This page is self-contained; no web server is needed. Meta-refresh reloads the file from disk while the tab stays open; each daemon run overwrites it.
    <br /><br />
    Use <strong>Theme</strong> (top right) for light or dark page colors; preference is stored in <code>localStorage</code> (<code>smsRipperTheme</code>).
    <br /><br />
    Use <strong>UTC</strong> / <strong>Local</strong> for time display (including the generated timestamp). Preference is saved in a <code>cookie</code> (<code>smsRipperTzDisplay</code>).
    <br /><br />
    The <strong>cycle</strong> column is a link icon when a row was archived under a scheduled daemon run; hover for the cycle start time. Target: <code>daemon-cycles/cycle_&lt;start-with-colons-as-dashes&gt;_&lt;pid&gt;.html</code>. Older rows or manual runs show —.
    <br /><br />
    The <strong>full</strong> column opens the complete archived <code>text</code> in a new browser window (popup blockers may require allowing this for <code>file://</code> if nothing opens).
    <br /><br />
    <a href="daemon-cycles/index.html">Daemon cycle log (static HTML)</a> — parsed from <code>logs/daemon.log</code> at end of each cycle.
  </div>
{html_tz_toggle.THEME_JS}
{html_tz_toggle.TOGGLE_JS}
{full_text_script}
</body>
</html>
"""


def _archive_has_cycle_columns(conn: sqlite3.Connection) -> bool:
    names = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM pragma_table_info(?)",
            (TABLE,),
        )
    }
    return "daemon_cycle_start" in names and "daemon_cycle_pid" in names


def _fetch_rows(conn: sqlite3.Connection, limit: int) -> tuple[int, list[dict[str, object]]]:
    cur = conn.execute(
        f"SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (TABLE,),
    )
    if not cur.fetchone():
        return 0, []

    total = int(conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0])
    cycle_sql_p = ""
    cycle_sql_bare = ""
    if _archive_has_cycle_columns(conn):
        cycle_sql_p = (
            ", p.daemon_cycle_start AS daemon_cycle_start, "
            "p.daemon_cycle_pid AS daemon_cycle_pid"
        )
        cycle_sql_bare = ", daemon_cycle_start, daemon_cycle_pid"
    # Join handle when both tables exist
    q = f"""
    SELECT p.rowid AS rowid, p.date AS date, p.text AS text, h.id AS handle{cycle_sql_p}
    FROM {TABLE} AS p
    LEFT JOIN handle AS h ON p.handle_id = h.rowid
    ORDER BY p.date DESC
    LIMIT ?
    """
    try:
        cur = conn.execute(q, (limit,))
    except sqlite3.Error:
        q2 = f"""
        SELECT rowid, date, text, NULL AS handle{cycle_sql_bare}
        FROM {TABLE} ORDER BY date DESC LIMIT ?
        """
        cur = conn.execute(q2, (limit,))
    out: list[dict[str, object]] = []
    for row in cur.fetchall():
        base = {
            "rowid": row[0],
            "date": row[1],
            "text": row[2],
            "handle": row[3],
        }
        if cycle_sql_p:
            base["daemon_cycle_start"] = row[4]
            base["daemon_cycle_pid"] = row[5]
        else:
            base["daemon_cycle_start"] = None
            base["daemon_cycle_pid"] = None
        out.append(base)
    return total, out


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate static HTML report for POLITICAL_archive")
    parser.add_argument("--chat-db", type=Path, default=None, help="Override chat.db path")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output HTML path")
    parser.add_argument("--limit", type=int, default=100, help="Max rows in table")
    args = parser.parse_args()

    db_path = Path(args.chat_db or config.CHAT_DB_PATH).expanduser()
    out_path = Path(args.output).expanduser()
    now_utc = datetime.now(timezone.utc)
    generated_at_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    generated_at_date = now_utc.strftime("%Y-%m-%d")
    generated_at_time = now_utc.strftime("%H:%M:%S UTC")

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
        generated_at_iso=generated_at_iso,
        generated_at_date=generated_at_date,
        generated_at_time=generated_at_time,
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
