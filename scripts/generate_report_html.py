#!/usr/bin/env python3
"""
Write a self-contained static HTML report of POLITICAL_archive (and summary stats).

Designed to run after each daemon cycle; open the file in a browser or bookmark a file:// URL.
Optional meta-refresh reloads the page periodically while it stays open (picks up new file content).

Usage:
  python scripts/generate_report_html.py
  python scripts/generate_report_html.py --output reports/index.html
  python scripts/generate_report_html.py --archive-training-url http://127.0.0.1:8765
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
from typing import Literal

_REPO = Path(__file__).resolve().parent.parent
_SCRIPTS = Path(__file__).resolve().parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import archive_tag_training as att  # noqa: E402
import classifier  # noqa: E402
import config  # noqa: E402
import html_tz_toggle  # noqa: E402
from reader import apple_ts_to_datetime  # noqa: E402

DEFAULT_OUTPUT = _REPO / "reports" / "index.html"
CHANGELOG_PATH = _REPO / "CHANGELOG.md"
TABLE = "POLITICAL_archive"
META_REFRESH_SEC = 900  # align with default daemon interval; 0 = disable

# Top-most `## …Z` or `## …Z UTC` section heading (footer shows the same `…Z UTC` text).
_CHANGELOG_HEADING_RE = re.compile(
    r"^##\s+(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)(?:\s+UTC)?\s*$",
    re.MULTILINE,
)


def _changelog_latest_entry_footer_text() -> str | None:
    """Same string as the heading after `## `, e.g. ``2026-03-30T20:30:00Z UTC` (footer matches)."""
    if not CHANGELOG_PATH.is_file():
        return None
    text = CHANGELOG_PATH.read_text(encoding="utf-8")
    m = _CHANGELOG_HEADING_RE.search(text)
    if not m:
        return None
    return f"{m.group(1)} UTC"


# Archive index <select id="archive-type-filter">; persisted by inline JS (path=/, 1y max-age).
ARCHIVE_TYPE_FILTER_COOKIE = "smsRipperArchiveTypeFilter"


def _js_archive_type_filter_cookie_block() -> str:
    """Shared helpers for static report + archive training server index (embedded in IIFE)."""
    key_js = json.dumps(ARCHIVE_TYPE_FILTER_COOKIE)
    return f"""  var ARCHIVE_TYPE_FILTER_KEY = {key_js};
  var ARCHIVE_FILTER_MAX_AGE = 31536000;
  function archiveTypeFilterGetCookie() {{
    var prefix = ARCHIVE_TYPE_FILTER_KEY + "=";
    var chunks = document.cookie.split(";");
    for (var i = 0; i < chunks.length; i++) {{
      var p = chunks[i].replace(/^\\s+/, "");
      if (p.indexOf(prefix) === 0)
        return decodeURIComponent(p.substring(prefix.length));
    }}
    return null;
  }}
  function archiveTypeFilterSetCookie(value) {{
    document.cookie =
      ARCHIVE_TYPE_FILTER_KEY +
      "=" +
      encodeURIComponent(value) +
      "; max-age=" +
      ARCHIVE_FILTER_MAX_AGE +
      "; path=/; SameSite=Lax";
  }}
  function archiveTypeFilterRestoreSelect(sel) {{
    var saved = archiveTypeFilterGetCookie();
    if (!saved) return;
    for (var i = 0; i < sel.options.length; i++) {{
      if (sel.options[i].value === saved) {{
        sel.selectedIndex = i;
        return;
      }}
    }}
  }}
"""

# Inline SVG (chain link) — self-contained for file://; no extension needed.
_SVG_CYCLE_LINK = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M4.715 6.542 3.343 7.914a3 3 0 1 0 4.243 4.243l1.828-1.829A3 3 0 0 0 8.586 5.5L8 6.086a1 1 0 0 0-.029 1.415l1.415 1.415a1 1 0 0 0 1.415-.029l1.829-1.828a3 3 0 1 0-4.243-4.243L6.343 4.542A3 3 0 0 0 4.5 6.386l.215.156zm7.794-2.894a1 1 0 0 0-1.414 1.414l1.415 1.415a1 1 0 0 0 1.414-1.414l-1.415-1.415z"/></svg>"""

# File + pop-out — open full body in a new window (Bootstrap-style file-earmark + corner).
_SVG_FULL_TEXT = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M14 4.5V14a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V2a2 2 0 0 1 2-2h5.5L14 4.5zM9.5 3V2H4a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1V5h-4.5A1.5 1.5 0 0 1 9.5 3zM5 6.5a.5.5 0 0 1 .5-.5h4a.5.5 0 0 1 0 1h-4a.5.5 0 0 1-.5-.5zm0 2a.5.5 0 0 1 .5-.5h4a.5.5 0 0 1 0 1h-4a.5.5 0 0 1-.5-.5zm0 2a.5.5 0 0 1 .5-.5h2a.5.5 0 0 1 0 1h-2a.5.5 0 0 1-.5-.5zM1 4h1v8H1V4zm2 3.5L5.5 10 8 7.5H6V5H4v2.5z"/></svg>"""


def _fmt_instant_utc_cell_html(dt_utc: datetime) -> str:
    """One UTC instant: same markup as the date column (dt-adjustable + date line + time line)."""
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    else:
        dt_utc = dt_utc.astimezone(timezone.utc)
    iso = dt_utc.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    utc_d = dt_utc.strftime("%Y-%m-%d")
    utc_t = dt_utc.strftime("%H:%M:%S UTC")
    return (
        f'<span class="dt-adjustable" data-utc="{html.escape(iso)}">'
        f"{html.escape(utc_d)}<br/>{html.escape(utc_t)}</span>"
    )


def _parse_utc_iso_string(s: str) -> datetime | None:
    """Parse stored ISO timestamps (e.g. utc_now_iso …Z) for reuse with _fmt_instant_utc_cell_html."""
    s = s.strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


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
    return _fmt_instant_utc_cell_html(dt_utc)


def _fmt_training_regenerate_at_html(re_at: str | None) -> str:
    """Same cell markup as date column; value is ISO from training Apply (archive meta)."""
    s = str(re_at or "").strip()
    if not s:
        return "—"
    dt = _parse_utc_iso_string(s)
    if dt is not None:
        return _fmt_instant_utc_cell_html(dt)
    if "T" in s:
        a, b = s.split("T", 1)
        return f"{html.escape(a)}<br/>{html.escape(b)}"
    return html.escape(s)


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


def _datetime_cell_inner_or_open_full(inner_html: str, rowid: object) -> str:
    """Plain em dash in date / last-retrain cells becomes same action as the full column."""
    if inner_html != "—":
        return inner_html
    try:
        rid_int = int(rowid)
    except (TypeError, ValueError):
        return inner_html
    label = f"Open full message text (rowid {rid_int}) in a new window"
    return (
        f'<button type="button" class="dash-open-full" '
        f'onclick="smsRipperOpenArchiveFull({rid_int})" '
        f'title="{html.escape(label)}" aria-label="{html.escape(label)}">—</button>'
    )


def _truncate(s: str | None, n: int = 240) -> str:
    if not s:
        return "—"
    t = " ".join(s.split())
    if len(t) <= n:
        return t
    return t[: n - 1] + "…"


def _parse_classifier_attributes(raw: object) -> list[str]:
    """Decode archive.classifier_attributes (legacy list or attributes+weights object)."""
    attrs, _ = classifier.decode_classifier_blob(raw)
    return attrs


def _build_html(
    *,
    generated_at_iso: str,
    generated_at_date: str,
    generated_at_time: str,
    total_archived: int,
    index_row_limit: int,
    rows: list[dict[str, object]],
    db_path: str,
    error: str | None,
    archive_training_url: str | None = None,
    index_variant: Literal["static_file", "training_server"] = "static_file",
) -> str:
    refresh_tag = ""
    if index_variant == "static_file" and META_REFRESH_SEC > 0:
        refresh_tag = (
            f'<meta http-equiv="refresh" content="{META_REFRESH_SEC}" />\n'
            f"<!-- Reloads every {META_REFRESH_SEC}s while open; file is rewritten each daemon run -->\n"
        )

    if index_variant == "training_server":
        full_text_script = """<script>
window.smsRipperOpenArchiveFull = function (rowid) {
  window.open("/message/" + String(rowid), "_blank");
};
</script>
"""
    else:
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
        training_url_json = json.dumps(archive_training_url) if archive_training_url else "null"
        full_text_script = f"""<script>
(function () {{
  window.SMS_RIPPER_ARCHIVE_FULL = {full_text_json};
  window.SMS_RIPPER_ARCHIVE_TRAINING_SERVER = {training_url_json};
  window.smsRipperOpenArchiveFull = function (rowid) {{
    var training = window.SMS_RIPPER_ARCHIVE_TRAINING_SERVER;
    if (training) {{
      var base = String(training).replace(/\\/+$/, "");
      window.open(base + "/message/" + String(rowid), "_blank");
      return;
    }}
    var map = window.SMS_RIPPER_ARCHIVE_FULL || {{}};
    var t = map[String(rowid)];
    if (t === undefined || t === null) t = "";
    // Do not use noopener here: browsers often return null from window.open, so we never
    // write to the new tab and the user sees a blank page.
    var w = window.open("about:blank", "_blank");
    if (!w || !w.document) return;
    var doc = w.document;
    doc.open();
    doc.write("<!DOCTYPE html><html lang=\\"en\\"><head><meta charset=\\"utf-8\\"><meta name=\\"viewport\\" content=\\"width=device-width, initial-scale=1\\"><title>Archived message " + String(rowid) + "</title><style>body{{margin:0;background:#111;color:#e8e8e8;font-family:system-ui,sans-serif;padding:1rem 1.25rem;line-height:1.5;white-space:pre-wrap;word-break:break-word;font-size:0.95rem;}}</style></head><body></body></html>");
    doc.close();
    if (doc.body) doc.body.textContent = t;
  }};
}})();
</script>
"""
    _cookie_js = _js_archive_type_filter_cookie_block()
    row_filter_script_static = f"""<script>
(function () {{
{_cookie_js}
  function applyFilter(sel) {{
    var v = sel ? sel.value : "all";
    var rows = document.querySelectorAll("tr.archive-row");
    var shown = 0;
    for (var i = 0; i < rows.length; i++) {{
      var row = rows[i];
      var raw = row.getAttribute("data-archive-types") || "";
      var tags = raw ? raw.split(/\\s+/) : [];
      var isUntagged = tags.length === 0;
      var show = false;
      if (v === "all") show = true;
      else if (v === "__untagged__") show = isUntagged;
      else if (v === "UNKNOWN")
        show =
          tags.indexOf("UNKNOWN") >= 0 ||
          row.getAttribute("data-archive-no-plaintext") === "1";
      else show = tags.indexOf(v) >= 0;
      row.style.display = show ? "" : "none";
      if (show) shown += 1;
    }}
    var c = document.getElementById("archive-filter-count");
    if (c) c.textContent = String(shown);
  }}
  function init() {{
    var sel = document.getElementById("archive-type-filter");
    if (!sel) return;
    archiveTypeFilterRestoreSelect(sel);
    sel.addEventListener("change", function () {{
      archiveTypeFilterSetCookie(sel.value);
      applyFilter(sel);
    }});
    applyFilter(sel);
  }}
  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", init);
  else
    init();
}})();
</script>
"""
    row_filter_script_training = f"""<script>
(function () {{
{_cookie_js}
  function applyFilter(sel) {{
    var v = sel ? sel.value : "all";
    var rows = document.querySelectorAll("tr.archive-row");
    var shown = 0;
    for (var i = 0; i < rows.length; i++) {{
      var row = rows[i];
      var raw = row.getAttribute("data-archive-types") || "";
      var tags = raw ? raw.split(/\\s+/) : [];
      var isUntagged = tags.length === 0;
      var retrain = (row.getAttribute("data-training-retrained") || "").trim();
      var show = false;
      if (v === "all") show = true;
      else if (v === "__untagged__") show = isUntagged;
      else if (v === "__retrained__") show = retrain.length > 0;
      else if (v === "UNKNOWN")
        show =
          tags.indexOf("UNKNOWN") >= 0 ||
          row.getAttribute("data-archive-no-plaintext") === "1";
      else show = tags.indexOf(v) >= 0;
      row.style.display = show ? "" : "none";
      if (show) shown += 1;
    }}
    var c = document.getElementById("archive-filter-count");
    if (c) c.textContent = String(shown);
  }}
  function init() {{
    var sel = document.getElementById("archive-type-filter");
    if (!sel) return;
    archiveTypeFilterRestoreSelect(sel);
    sel.addEventListener("change", function () {{
      archiveTypeFilterSetCookie(sel.value);
      applyFilter(sel);
    }});
    applyFilter(sel);
  }}
  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", init);
  else
    init();
}})();
</script>
"""
    row_filter_script = (
        row_filter_script_training
        if index_variant == "training_server"
        else row_filter_script_static
    )

    tbl_colspan = 7 if index_variant == "training_server" else 6

    if error:
        body = f"<p class=\"err\">{html.escape(error)}</p>"
        rows_html = (
            f'<tr><td colspan="{tbl_colspan}" class="err">Could not load archive rows.</td></tr>'
        )
        type_options_html = '<option value="all" selected>All types</option>'
    else:
        body = (
            f"<p><strong>{total_archived:,}</strong> total row(s) in the archives · "
            f"database <code>{html.escape(db_path)}</code></p>"
        )
        # Dropdown: union of tags present in this page plus UNKNOWN. UNKNOWN is often absent from
        # stored JSON while rows look "empty": live classification used RICH_ONLY_PLACEHOLDER (see
        # reader.py), but the archive copies empty message.text from chat.db.
        tag_values = sorted(
            {t for r in rows for t in (r.get("archive_types") or [])} | {"UNKNOWN"}
        )
        has_untagged = any(not (r.get("archive_types") or []) for r in rows)
        type_options = ['<option value="all" selected>All types</option>']
        for t in tag_values:
            type_options.append(
                f'<option value="{html.escape(t)}">{html.escape(t)}</option>'
            )
        if has_untagged:
            type_options.append('<option value="__untagged__">Untagged</option>')
        if index_variant == "training_server":
            type_options.append(
                '<option value="__retrained__">Retrained (training UI)</option>'
            )
        type_options_html = "\n".join(type_options)
        parts = []
        for r in rows:
            types = [str(t).strip() for t in (r.get("archive_types") or []) if str(t).strip()]
            tags_attr = html.escape(" ".join(types))
            no_plain = not str(r.get("text") or "").strip()
            no_plain_attr = ' data-archive-no-plaintext="1"' if no_plain else ""
            re_at = str(r.get("training_regenerated_at") or "").strip()
            train_attr = html.escape(re_at, quote=True)
            if index_variant == "training_server":
                train_inner = _fmt_training_regenerate_at_html(re_at)
                train_cell = (
                    f'<td class="ts-dual col-datetime" title="Last Apply from training UI">'
                    f"{_datetime_cell_inner_or_open_full(train_inner, r.get('rowid'))}</td>"
                )
                row_open = (
                    f'<tr class="archive-row" data-archive-types="{tags_attr}" '
                    f'data-training-retrained="{train_attr}"{no_plain_attr}>'
                )
            else:
                train_cell = ""
                row_open = (
                    f'<tr class="archive-row" data-archive-types="{tags_attr}"{no_plain_attr}>'
                )
            parts.append(
                f"{row_open}"
                f"<td>{html.escape(str(r.get('rowid', '')))}</td>"
                f"<td class=\"ts-dual col-datetime\">"
                f"{_datetime_cell_inner_or_open_full(_fmt_apple_date_cell_html(r.get('date')), r.get('rowid'))}"
                f"</td>"
                f"<td class=\"mono\">{html.escape(_truncate(str(r.get('handle') or '')))}</td>"
                f"<td class=\"cycle-link\">{_cycle_cell(r)}</td>"
                f"<td>{html.escape(_truncate(str(r.get('text') or '')))}</td>"
                f"{train_cell}"
                f"<td class=\"text-full-col\">{_full_text_open_button(r.get('rowid'))}</td>"
                "</tr>"
            )
        rows_html = (
            "\n".join(parts)
            if parts
            else f'<tr><td colspan="{tbl_colspan}">(no rows)</td></tr>'
        )

    if index_variant == "training_server":
        meta_blurb = (
            f'Generated <span class="dt-adjustable" data-utc="{html.escape(generated_at_iso)}">'
            f"{html.escape(generated_at_date)}<br/>{html.escape(generated_at_time)}</span>"
            " · served by <code>archive_training_server</code>; reload this page to refresh from "
            "<code>chat.db</code>"
        )
        hint_html = ""
    else:
        meta_blurb = (
            f'Generated <span class="dt-adjustable" data-utc="{html.escape(generated_at_iso)}">'
            f"{html.escape(generated_at_date)}<br/>{html.escape(generated_at_time)}</span>"
            " · updates when <code>generate_report_html.py</code> runs (e.g. end of each daemon cycle)"
        )
        hint_html = f"""<div class="hint">
    Open via Finder double-click or <code>open …/reports/index.html</code> (adjust if you used <code>--output</code>), then bookmark the <code>file://</code> URL.
    This page is self-contained; no web server is needed. Meta-refresh reloads the file from disk while the tab stays open; each daemon run overwrites it.
    <br /><br />
    Use <strong>Theme</strong> (top right) for light or dark page colors; preference is stored in <code>localStorage</code> (<code>smsRipperTheme</code>).
    <br /><br />
    Use <strong>UTC</strong> / <strong>Local</strong> for time display (including the generated timestamp). Preference is saved in a <code>cookie</code> (<code>smsRipperTzDisplay</code>).
    <br /><br />
    The <strong>cycle</strong> column is a link icon when a row was archived under a scheduled daemon run; hover for the cycle start time. Target: <code>daemon-cycles/cycle_&lt;start-with-colons-as-dashes&gt;_&lt;pid&gt;.html</code>. Older rows or manual runs show —.
    <br /><br />
    The <strong>full</strong> column opens the complete archived <code>text</code> in a new browser window, or the tag-training UI when the report was built with <code>--archive-training-url</code> (training server must be running).
    <br /><br />
    <a href="daemon-cycles/index.html">Daemon cycle log (static HTML)</a> — parsed from <code>logs/daemon.log</code> at end of each cycle.
  </div>"""

    if index_variant == "training_server":
        thead_html = """<thead><tr><th>rowid</th><th class="col-datetime" title="Message instant in UTC; use UTC / Local buttons for your browser">date</th><th>handle</th><th title="Daemon cycle log (same slug as reports/daemon-cycles/)">cycle</th><th>text (truncated)</th><th class="col-datetime" title="UTC time of last Apply from training UI (human hints); use UTC / Local buttons for your browser">last retrain</th><th title="Open full message body in a new window">full</th></tr></thead>"""
    else:
        thead_html = """<thead><tr><th>rowid</th><th class="col-datetime" title="Message instant in UTC; use UTC / Local buttons for your browser">date</th><th>handle</th><th title="Daemon cycle log (same slug as reports/daemon-cycles/)">cycle</th><th>text (truncated)</th><th title="Open full message body in a new window">full</th></tr></thead>"""

    changelog_href = "/CHANGELOG.md" if index_variant == "training_server" else "../CHANGELOG.md"
    latest_entry = _changelog_latest_entry_footer_text()
    if latest_entry:
        changelog_footer_html = (
            f'<p class="footer-changelog"><a href="{html.escape(changelog_href)}">CHANGELOG.md</a>'
            f'<span class="footer-changelog-mtime"> · latest entry {html.escape(latest_entry)}</span></p>'
        )
    else:
        changelog_footer_html = (
            f'<p class="footer-changelog"><a href="{html.escape(changelog_href)}">CHANGELOG.md</a></p>'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  {refresh_tag}  <title>sms-ripper — Message Archive Report</title>
  <style>
    {html_tz_toggle.THEME_CSS}
    :root {{ font-family: system-ui, sans-serif; }}
    html {{ background: var(--sr-bg-page); color: var(--sr-fg); }}
    body {{ max-width: 1100px; margin: 2rem auto; padding: 0 1rem; }}
    a {{ color: var(--sr-link); }}
    a:visited {{ color: var(--sr-link-visited); }}
    a:hover {{ color: var(--sr-link-hover); }}
    {html_tz_toggle.HERO_H1_CSS}
    h2 {{ font-size: 1em; }}
    .meta {{ color: var(--sr-fg-muted); font-size: 0.9rem; margin-bottom: 1.5rem; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
    th, td {{ border: 1px solid var(--sr-border); padding: 0.5rem 0.6rem; text-align: left; vertical-align: top; }}
    th {{ background: var(--sr-th-bg); }}
    tr:nth-child(even) {{ background: var(--sr-tr-alt); }}
    .mono {{ font-family: ui-monospace, monospace; max-width: 12rem; word-break: break-all; }}
    td.ts-dual {{ font-size: 0.82rem; line-height: 1.35; vertical-align: top; word-break: break-word; }}
    button.dash-open-full {{
      font: inherit;
      font-size: inherit;
      line-height: inherit;
      color: inherit;
      background: none;
      border: none;
      padding: 0;
      margin: 0;
      cursor: pointer;
      text-align: left;
      width: 100%;
    }}
    button.dash-open-full:hover {{ color: var(--sr-link-hover); text-decoration: underline; }}
    .cycle-link {{ text-align: center; width: 2.75rem; vertical-align: middle; }}
    a.icon-nav, button.icon-nav {{ color: var(--sr-link); text-decoration: none; display: inline-flex; align-items: center; justify-content: center; }}
    button.icon-nav {{ border: none; background: transparent; padding: 0; cursor: pointer; font: inherit; }}
    a.icon-nav:hover, button.icon-nav:hover {{ color: var(--sr-link-hover); }}
    a.icon-nav svg, button.icon-nav svg {{ display: block; }}
    .text-full-col {{ text-align: center; width: 2.75rem; vertical-align: middle; }}
    .hint {{ background: var(--sr-hint-bg); border: 1px solid var(--sr-hint-border); padding: 0.75rem 1rem; margin-top: 1.5rem; font-size: 0.85rem; }}
    .err {{ color: var(--sr-err); }}
    code {{ font-size: 0.9em; }}
    .filter-row {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.6rem;
      margin: 0 0 0.8rem;
    }}
    .filter-row label {{
      color: var(--sr-fg-muted);
      font-size: 0.9rem;
      line-height: 1.4;
      margin: 0;
    }}
    .filter-row select {{
      font: inherit;
      font-size: 0.9rem;
      line-height: 1.4;
      background: var(--sr-th-bg);
      color: var(--sr-fg);
      border: 1px solid var(--sr-border);
      border-radius: 4px;
      padding: 0.2rem 0.45rem;
      margin: 0;
    }}
    /* Reuse .meta colors but not paragraph spacing (margin-bottom breaks flex cross-axis centering). */
    .filter-row .meta {{
      color: var(--sr-fg-muted);
      font-size: 0.9rem;
      line-height: 1.4;
      margin: 0;
    }}
    .filter-row .meta strong {{ font-weight: 600; }}
    .footer-changelog {{ margin-top: 1.75rem; font-size: 0.85rem; color: var(--sr-fg-muted); }}
    .footer-changelog-mtime {{ font-weight: 400; }}
    {html_tz_toggle.TOGGLE_CSS}
  </style>
{html_tz_toggle.THEME_BOOTSTRAP_HEAD}
</head>
<body>
{html_tz_toggle.TOGGLE_HTML}
  <h1>Message Archive Report</h1>
  <p class="meta">{meta_blurb}</p>
  {body}
  <h2>Latest {index_row_limit} archived messages (newest first)</h2>
  <div class="filter-row">
    <label for="archive-type-filter">Archive type</label>
    <select id="archive-type-filter">{type_options_html}</select>
    <span class="meta">Showing <strong id="archive-filter-count">0</strong> row(s)</span>
  </div>
  <table>
    {thead_html}
    <tbody>
{rows_html}
    </tbody>
  </table>
{hint_html}
{changelog_footer_html}
{html_tz_toggle.THEME_JS}
{html_tz_toggle.TOGGLE_JS}
{row_filter_script}
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


def _archive_has_classifier_attributes(conn: sqlite3.Connection) -> bool:
    names = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM pragma_table_info(?)",
            (TABLE,),
        )
    }
    return "classifier_attributes" in names


def _fetch_rows(conn: sqlite3.Connection, limit: int) -> tuple[int, list[dict[str, object]]]:
    cur = conn.execute(
        f"SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (TABLE,),
    )
    if not cur.fetchone():
        return 0, []

    total = int(conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0])
    has_cycle = _archive_has_cycle_columns(conn)
    has_classifier = _archive_has_classifier_attributes(conn)
    cycle_sql_p = ""
    cycle_sql_bare = ""
    classifier_sql_p = ""
    classifier_sql_bare = ""
    if has_cycle:
        cycle_sql_p = ", p.daemon_cycle_start AS daemon_cycle_start, p.daemon_cycle_pid AS daemon_cycle_pid"
        cycle_sql_bare = ", daemon_cycle_start, daemon_cycle_pid"
    if has_classifier:
        classifier_sql_p = ", p.classifier_attributes AS classifier_attributes"
        classifier_sql_bare = ", classifier_attributes"
    # Join handle when both tables exist
    q = f"""
    SELECT p.rowid AS rowid, p.date AS date, p.text AS text, h.id AS handle{cycle_sql_p}{classifier_sql_p}
    FROM {TABLE} AS p
    LEFT JOIN handle AS h ON p.handle_id = h.rowid
    ORDER BY p.date DESC
    LIMIT ?
    """
    try:
        cur = conn.execute(q, (limit,))
    except sqlite3.Error:
        q2 = f"""
        SELECT rowid, date, text, NULL AS handle{cycle_sql_bare}{classifier_sql_bare}
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
        idx = 4
        if has_cycle:
            base["daemon_cycle_start"] = row[idx]
            base["daemon_cycle_pid"] = row[idx + 1]
            idx += 2
        else:
            base["daemon_cycle_start"] = None
            base["daemon_cycle_pid"] = None
        if has_classifier:
            base["archive_types"] = _parse_classifier_attributes(row[idx])
        else:
            base["archive_types"] = []
        out.append(base)
    return total, out


def build_training_server_index_html(
    conn: sqlite3.Connection,
    *,
    limit: int,
    db_path: str,
) -> str:
    """
    Same layout as the static political archive report, for ``GET /`` on
    ``archive_training_server.py``: the **full** control opens ``/message/<rowid>`` on the
    same origin (no embedded message map or ``--archive-training-url``).
    """
    now_utc = datetime.now(timezone.utc)
    generated_at_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    generated_at_date = now_utc.strftime("%Y-%m-%d")
    generated_at_time = now_utc.strftime("%H:%M:%S UTC")
    error: str | None = None
    total = 0
    rows: list[dict[str, object]] = []
    try:
        total, rows = _fetch_rows(conn, max(1, limit))
        rowids = [int(r["rowid"]) for r in rows if r.get("rowid") is not None]
        times = att.fetch_training_regenerate_times(conn, rowids)
        for r in rows:
            rid = r.get("rowid")
            if rid is not None:
                r["training_regenerated_at"] = times.get(int(rid), "")
    except Exception as e:
        error = f"{e}\n{traceback.format_exc()}"
    return _build_html(
        generated_at_iso=generated_at_iso,
        generated_at_date=generated_at_date,
        generated_at_time=generated_at_time,
        total_archived=total,
        index_row_limit=max(1, limit),
        rows=rows,
        db_path=db_path,
        error=error,
        index_variant="training_server",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate static HTML report for POLITICAL_archive")
    parser.add_argument("--chat-db", type=Path, default=None, help="Override chat.db path")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output HTML path")
    parser.add_argument("--limit", type=int, default=100, help="Max rows in table")
    parser.add_argument(
        "--archive-training-url",
        default=None,
        metavar="URL",
        help="If set, Full opens this base URL + /message/<rowid> (e.g. http://127.0.0.1:8765)",
    )
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
        index_row_limit=max(1, args.limit),
        rows=rows,
        db_path=str(db_path),
        error=error,
        archive_training_url=args.archive_training_url,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_doc, encoding="utf-8")
    print(f"Wrote {out_path}")
    if error:
        print(error, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
