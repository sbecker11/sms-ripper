#!/usr/bin/env python3
"""
Local HTTP UI to review and iteratively re-run LLM tag classification on message_tags_archive rows.

Binds to loopback only. **Quit Messages.app** before running if chat.db is the live library
(see project README) so the DB is not locked.

Usage:
  python scripts/archive_training_server.py
  python scripts/archive_training_server.py --port 8765 --limit 100 --chat-db ~/Library/Messages/chat.db
  python scripts/archive_training_server.py --no-browser
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

_REPO = Path(__file__).resolve().parent.parent
_SCRIPTS = Path(__file__).resolve().parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import archive  # noqa: E402
import archive_tag_training as att  # noqa: E402
import config  # noqa: E402
import generate_report_html as report_html  # noqa: E402
import html_tz_toggle  # noqa: E402
import tag_catalog  # noqa: E402

_REPORTS_DIR = _REPO / "reports"
_DAEMON_CYCLES_DIR = _REPORTS_DIR / "daemon-cycles"


def _archive_table(conn: sqlite3.Connection) -> str:
    return archive.require_archive_table(conn, "education")


def _training_tags_set(conn: sqlite3.Connection) -> set[str]:
    return set(att._training_tags(conn))


def _archive_column_names(conn: sqlite3.Connection) -> set[str]:
    table = _archive_table(conn)
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _fetch_archive_message(conn: sqlite3.Connection, rowid: int) -> dict[str, object] | None:
    if not _archive_column_names(conn):
        return None
    table = _archive_table(conn)
    cols = _archive_column_names(conn)
    select_cols: list[str] = ["p.rowid", "p.date", "p.text"]
    if "subject" in cols:
        select_cols.append("p.subject AS subject")
    else:
        select_cols.append("NULL AS subject")
    select_cols.append("h.id AS handle")
    if "classifier_attributes" in cols:
        select_cols.append("p.classifier_attributes AS classifier_attributes")
    else:
        select_cols.append("NULL AS classifier_attributes")

    q = (
        f"SELECT {', '.join(select_cols)} "
        f"FROM {table} AS p "
        f"LEFT JOIN handle AS h ON p.handle_id = h.rowid "
        f"WHERE p.rowid = ?"
    )
    row = conn.execute(q, (rowid,)).fetchone()
    if not row:
        return None
    return {
        "rowid": row[0],
        "date_ns": row[1],
        "text": row[2] if row[2] is not None else "",
        "subject": row[3],
        "handle": row[4],
        "classifier_attributes": row[5],
    }


def _json_bytes(obj: object, status: int = 200) -> tuple[int, bytes, str]:
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return status, body, "application/json; charset=utf-8"


def _page_html(rowid: int, reviewer_id: str = "") -> str:
    ht = html_tz_toggle
    reviewer_id_json = json.dumps((reviewer_id or "").strip(), ensure_ascii=False)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>sms-ripper — Archive message training · row {rowid}</title>
  <style>
    {ht.THEME_CSS}
    :root {{ font-family: system-ui, sans-serif; }}
    * {{ box-sizing: border-box; }}
    html {{ background: var(--sr-bg-page); color: var(--sr-fg); }}
    body {{
      max-width: 1100px;
      margin: 2rem auto;
      padding: 0 1rem 5.5rem;
      line-height: 1.45;
      font-size: 0.9rem;
    }}
    {ht.HERO_H1_CSS}
    .meta {{ color: var(--sr-fg-muted); font-size: 0.9rem; margin-bottom: 1rem; }}
    .meta span {{ margin-right: 1rem; }}
    .msg {{
      background: var(--sr-th-bg);
      border: 1px solid var(--sr-border);
      border-radius: 8px;
      padding: 0.85rem 1rem;
      margin-bottom: 1rem;
      white-space: pre-wrap;
      word-break: break-word;
    }}
    .subj {{ font-weight: 600; margin-bottom: 0.5rem; }}
    table#archive-training-tag-table {{
      table-layout: fixed;
      width: 100%;
      border-collapse: separate;
      border-spacing: 0;
      margin-top: 0.5rem;
      font-size: 0.85rem;
    }}
    /* Explicit column widths so the keywords column (width:100% inputs) cannot steal space
       from tag/on/w/source; checkboxes use transform:scale globally — disable here so paint
       does not spill into adjacent cells. */
    table#archive-training-tag-table col.at-col-tag {{ width: 9.5rem; }}
    table#archive-training-tag-table col.at-col-on {{ width: 3.5rem; }}
    table#archive-training-tag-table col.at-col-w {{ width: 4rem; }}
    table#archive-training-tag-table col.at-col-src {{ width: 5.5rem; }}
    #archive-training-tag-table th:nth-child(1),
    #archive-training-tag-table td:nth-child(1) {{
      overflow-wrap: break-word;
      word-break: break-word;
      vertical-align: middle;
    }}
    #archive-training-tag-table th:nth-child(2),
    #archive-training-tag-table td:nth-child(2) {{
      text-align: center;
      vertical-align: middle;
    }}
    #archive-training-tag-table th:nth-child(3),
    #archive-training-tag-table td:nth-child(3) {{
      text-align: right;
      vertical-align: middle;
    }}
    #archive-training-tag-table th:nth-child(4),
    #archive-training-tag-table td:nth-child(4) {{
      white-space: nowrap;
      vertical-align: middle;
    }}
    #archive-training-tag-table th:nth-child(5),
    #archive-training-tag-table td:nth-child(5) {{
      vertical-align: top;
    }}
    #archive-training-tag-table input[type="checkbox"] {{
      transform: none;
      accent-color: var(--sr-link);
      margin: 0;
      vertical-align: middle;
    }}
    #archive-training-tag-table thead th {{
      border: 1px solid var(--sr-border);
      color: var(--sr-fg-muted);
      font-weight: 500;
      font-size: 0.75rem;
      text-transform: uppercase;
      background: var(--sr-th-bg);
    }}
    tbody.train-pair tr td {{
      border-left: 1px solid var(--sr-border);
      border-right: 1px solid var(--sr-border);
      border-bottom: 1px solid var(--sr-border);
      border-top: none;
    }}
    tbody.train-pair tr:first-child td {{
      border-top: 3px solid var(--sr-border);
    }}
    tbody.train-pair tr:last-child td {{
      border-bottom-width: 3px;
    }}
    tbody.train-pair tr td:last-child {{
      border-right-width: 3px;
    }}
    tbody.train-pair tr.human td:first-child,
    tbody.train-pair tr.llm td:first-child {{
      border-left-width: 4px;
      border-left-color: color-mix(in srgb, var(--sr-link) 35%, var(--sr-border));
    }}
    tbody.train-pair tr:first-child td:first-child {{ border-top-left-radius: 5px; }}
    tbody.train-pair tr:first-child td:last-child {{ border-top-right-radius: 5px; }}
    tbody.train-pair tr:last-child td:first-child {{ border-bottom-left-radius: 5px; }}
    tbody.train-pair tr:last-child td:last-child {{ border-bottom-right-radius: 5px; }}
    th, td {{
      padding: 0.5rem 0.6rem;
      text-align: left;
      vertical-align: top;
    }}
    tbody.train-pair tr.llm td {{
      background: color-mix(in srgb, var(--sr-tr-alt) 32%, var(--sr-bg-page));
      color: color-mix(in srgb, var(--sr-fg) 78%, var(--sr-bg-page));
    }}
    tbody.train-pair tr.human td {{ background: transparent; }}
    .tag {{ font-weight: 600; }}
    td.wcol {{ font-variant-numeric: tabular-nums; }}
    input[type="text"] {{
      width: 100%;
      background: var(--sr-pre-bg);
      border: 1px solid var(--sr-border);
      color: var(--sr-fg);
      border-radius: 4px;
      padding: 0.25rem 0.4rem;
      font-size: 0.82rem;
    }}
    input[type="checkbox"] {{ transform: scale(1.1); accent-color: var(--sr-link); }}
    .kw {{ font-size: 0.8rem; color: var(--sr-fg-muted); }}
    footer {{
      position: fixed;
      bottom: 0;
      left: 0;
      right: 0;
      padding: 0.75rem 1.25rem;
      background: var(--sr-bar-bg);
      border-top: 1px solid var(--sr-bar-border);
      box-shadow: 0 -2px 8px var(--sr-bar-shadow);
      display: flex;
      gap: 0.75rem;
      justify-content: flex-end;
    }}
    footer button {{
      font: inherit;
      padding: 0.45rem 1rem;
      border-radius: 6px;
      border: 1px solid var(--sr-bar-btn-border);
      background: var(--sr-bar-btn-bg);
      color: var(--sr-bar-btn-fg);
      cursor: pointer;
    }}
    footer button:hover {{
      color: var(--sr-bar-btn-hover-fg);
      border-color: var(--sr-bar-btn-hover-border);
    }}
    footer button.primary {{
      background: var(--sr-bar-btn-active-bg);
      border-color: var(--sr-bar-btn-active-border);
      color: var(--sr-bar-btn-active-fg);
    }}
    footer button:disabled {{ opacity: 0.5; cursor: not-allowed; }}
    .err {{ color: var(--sr-err); margin: 0.5rem 0; }}
    .reason {{ font-size: 0.82rem; color: var(--sr-fg-muted); margin-top: 0.75rem; }}
    .guard-edit label {{
      display: block;
      font-size: 0.72rem;
      color: var(--sr-fg-muted);
      margin: 0.18rem 0 0.12rem;
    }}
    .guard-edit input[type="text"] {{
      width: 100%;
      margin-bottom: 0.2rem;
    }}
    .guard-pills {{
      margin-top: 0.25rem;
      display: flex;
      flex-wrap: wrap;
      gap: 0.25rem;
    }}
    .guard-pill {{
      display: inline-block;
      padding: 0.1rem 0.35rem;
      border: 1px solid var(--sr-border);
      border-radius: 999px;
      font-size: 0.7rem;
      color: var(--sr-fg-muted);
    }}
    .guard-pill.inc {{ border-color: color-mix(in srgb, #4caf50 45%, var(--sr-border)); }}
    .guard-pill.exc {{ border-color: color-mix(in srgb, #e53935 45%, var(--sr-border)); }}
    .guard-preview {{
      margin-top: 0.75rem;
      font-size: 0.78rem;
      color: var(--sr-fg-muted);
      white-space: pre-wrap;
    }}
    {ht.TOGGLE_CSS}
  </style>
{ht.THEME_BOOTSTRAP_HEAD}
</head>
<body>
{ht.TOGGLE_HTML}
    <h1>Archive message training</h1>
    <div class="meta" id="meta"></div>
    <div class="msg" id="body"></div>
    <p class="reason" id="reason"></p>
    <table id="archive-training-tag-table">
      <colgroup>
        <col class="at-col-tag" />
        <col class="at-col-on" />
        <col class="at-col-w" />
        <col class="at-col-src" />
        <col class="at-col-kw" />
      </colgroup>
      <thead><tr><th>Tag</th><th>On</th><th title="LLM confidence 0–1">W</th><th>Source</th><th>Keywords / hints</th></tr></thead>
    </table>
    <div id="guard-preview" class="guard-preview"></div>
    <p class="err" id="err" hidden></p>
  <footer>
    <button type="button" id="done">Done</button>
    <button type="button" class="primary" id="apply">Apply</button>
  </footer>
{ht.THEME_JS}
{ht.TOGGLE_JS}
  <script>
(function () {{
  const ROWID = {int(rowid)};
  const REVIEWER_ID = {reviewer_id_json};
  const metaEl = document.getElementById("meta");
  const bodyEl = document.getElementById("body");
  const tagTable = document.getElementById("archive-training-tag-table");
  const errEl = document.getElementById("err");
  const reasonEl = document.getElementById("reason");
  const applyBtn = document.getElementById("apply");
  const guardPreviewEl = document.getElementById("guard-preview");

  function closeTrainingTab() {{
    try {{
      if (window.opener && !window.opener.closed) {{
        window.opener.location.reload();
      }}
    }} catch (e) {{}}
    window.close();
  }}

  function showErr(msg) {{
    errEl.textContent = msg || "";
    errEl.hidden = !msg;
  }}

  function fmtWeight(w) {{
    if (w == null || w === "") return "—";
    var n = Number(w);
    if (!isFinite(n)) return "—";
    return n.toFixed(2);
  }}

  function fmtNs(ns) {{
    if (ns == null || ns === "") return "—";
    var n = Number(ns);
    if (!isFinite(n)) return String(ns);
    // Apple message.date stores nanoseconds since 2001-01-01 00:00:00 UTC.
    var sec = Math.floor(n / 1000000000) + 978307200;
    try {{
      return new Date(sec * 1000).toISOString().replace("T", " ").replace("Z", " UTC");
    }} catch (e) {{ return String(ns); }}
  }}

  function fmtNsISO(ns) {{
    if (ns == null || ns === "") return "";
    var n = Number(ns);
    if (!isFinite(n)) return String(ns);
    // Apple message.date stores nanoseconds since 2001-01-01 00:00:00 UTC.
    var sec = Math.floor(n / 1000000000) + 978307200;
    try {{
      return new Date(sec * 1000).toISOString();
    }} catch (e) {{ return String(ns); }}
  }}

  function getCookie(name) {{
    var prefix = name + "=";
    var chunks = document.cookie.split(";");
    for (var i = 0; i < chunks.length; i++) {{
      var p = chunks[i].replace(/^\\s+/, "");
      if (p.indexOf(prefix) === 0) return decodeURIComponent(p.substring(prefix.length));
    }}
    return null;
  }}

  function pad2(n) {{ return (n < 10 ? "0" : "") + n; }}

  function tzAbbrevShort(d) {{
    var abbr = "";
    try {{
      var parts = new Intl.DateTimeFormat(undefined, {{ timeZoneName: "short" }}).formatToParts(d);
      for (var j = 0; j < parts.length; j++) {{
        if (parts[j].type === "timeZoneName") {{
          abbr = parts[j].value;
          break;
        }}
      }}
    }} catch (e) {{
      return "";
    }}
    if (!abbr) return "";
    if (/GMT|UTC/i.test(abbr) && /[+-]\\d/.test(abbr)) return "";
    if (/[+-]\\d/.test(abbr)) return "";
    return abbr;
  }}

  function fmtUTC(d) {{
    var y = d.getUTCFullYear() + "-" + pad2(d.getUTCMonth() + 1) + "-" + pad2(d.getUTCDate());
    var t =
      pad2(d.getUTCHours()) +
      ":" +
      pad2(d.getUTCMinutes()) +
      ":" +
      pad2(d.getUTCSeconds()) +
      " UTC";
    return y + "<br>" + t;
  }}

  function fmtLocal(d) {{
    try {{
      var dateLine = new Intl.DateTimeFormat(undefined, {{ year: "numeric", month: "short", day: "numeric" }}).format(d);
      var timeLine = new Intl.DateTimeFormat(undefined, {{ hour: "numeric", minute: "2-digit", second: "2-digit", hour12: true }}).format(d);
      var abbr = tzAbbrevShort(d);
      return dateLine + "<br>" + timeLine + (abbr ? " " + abbr : "");
    }} catch (e) {{
      return String(d);
    }}
  }}

  function applyTzToDtAdjustables(mode) {{
    var els = document.querySelectorAll("[data-utc]");
    for (var i = 0; i < els.length; i++) {{
      var el = els[i];
      var raw = el.getAttribute("data-utc");
      if (!raw) continue;
      var d = new Date(raw);
      if (isNaN(d.getTime())) continue;
      el.innerHTML = mode === "local" ? fmtLocal(d) : fmtUTC(d);
    }}
  }}

  function render(data) {{
    showErr("");
    var tzMode = (getCookie("smsRipperTzDisplay") || "utc").trim().toLowerCase();
    if (tzMode !== "local" && tzMode !== "utc") tzMode = "utc";
    var dateIso = fmtNsISO(data.date_ns);
    metaEl.innerHTML =
      "<span><strong>rowid</strong> " + data.rowid + "</span>" +
      "<span><strong>reviewer</strong> " + (REVIEWER_ID || "unknown") + "</span>" +
      "<span><strong>date</strong> " +
        "<span class='dt-adjustable' data-utc='" + dateIso + "'>" + fmtNs(data.date_ns) + "</span>" +
      "</span>" +
      "<span><strong>handle</strong> " + (data.handle || "—") + "</span>" +
      "<span><strong>snapshot</strong> " + (data.generated_at_iso || "—") + "</span>" +
      "<span><strong>last retrain</strong> " + (data.last_training_regenerate_at || "—") + "</span>";
    // Ensure date display matches current UTC/Local cookie even if the shared TOGGLE_JS
    // ran before this page’s async `render()` injected the date element.
    if (dateIso) applyTzToDtAdjustables(tzMode);
    var subj = (data.subject || "").trim();
    var txt = data.text != null ? String(data.text) : "";
    bodyEl.innerHTML = (subj ? "<div class=\\"subj\\">" + escapeHtml(subj) + "</div>" : "") +
      escapeHtml(txt);
    reasonEl.textContent = data.last_llm_reason
      ? ("Last model note: " + data.last_llm_reason)
      : "";
    tagTable.querySelectorAll("tbody.train-pair").forEach(function (el) {{ el.remove(); }});
    (data.tags || []).forEach(function (t) {{
      var tag = escapeHtml(t.tag);
      var llmKw = escapeHtml(t.llm_keywords || "");
      var humKw = String(t.human_keywords || "");
      var trHuman = document.createElement("tr");
      trHuman.className = "human";
      trHuman.dataset.tag = t.tag;
      trHuman.dataset.modelInclude = String(t.model_include_guards || "");
      trHuman.dataset.modelExclude = String(t.model_exclude_guards || "");
      trHuman.innerHTML =
        "<td class=\\"tag\\">" + tag + "</td>" +
        "<td><input type=\\"checkbox\\" class=\\"hum-check\\" " + (t.human_checked ? "checked" : "") + " /></td>" +
        "<td class=\\"mono wcol\\">—</td>" +
        "<td>Human</td>" +
        "<td><div class=\\"guard-edit\\">" +
          "<label>Message hints</label><input type=\\"text\\" class=\\"hum-kw\\" />" +
          "<label>Add include guards</label><input type=\\"text\\" class=\\"hum-inc\\" placeholder=\\"term1, term2\\" />" +
          "<label>Add exclude guards</label><input type=\\"text\\" class=\\"hum-exc\\" placeholder=\\"term1, term2\\" />" +
        "</div></td>";
      trHuman.querySelector(".hum-kw").value = humKw;
      trHuman.querySelector(".hum-kw").addEventListener("input", updateGuardPreview);
      trHuman.querySelector(".hum-inc").addEventListener("input", updateGuardPreview);
      trHuman.querySelector(".hum-exc").addEventListener("input", updateGuardPreview);
      trHuman.querySelector(".hum-check").addEventListener("change", updateGuardPreview);
      var trLlm = document.createElement("tr");
      trLlm.className = "llm";
      var pills = "";
      var incGuards = String(t.model_include_guards || "").trim();
      var excGuards = String(t.model_exclude_guards || "").trim();
      if (incGuards || excGuards) {{
        pills += "<div class=\\"guard-pills\\">";
        if (incGuards) pills += "<span class=\\"guard-pill inc\\">+ " + escapeHtml(incGuards) + "</span>";
        if (excGuards) pills += "<span class=\\"guard-pill exc\\">- " + escapeHtml(excGuards) + "</span>";
        pills += "</div>";
      }}
      trLlm.innerHTML =
        "<td class=\\"tag\\">" + tag + "</td>" +
        "<td><input type=\\"checkbox\\" disabled " + (t.llm_checked ? "checked" : "") + " /></td>" +
        "<td class=\\"mono wcol\\">" + escapeHtml(fmtWeight(t.llm_weight)) + "</td>" +
        "<td>LLM</td>" +
        "<td class=\\"kw\\">" + llmKw + pills + "</td>";
      var tb = document.createElement("tbody");
      tb.className = "train-pair";
      tb.appendChild(trHuman);
      tb.appendChild(trLlm);
      tagTable.appendChild(tb);
    }});
    updateGuardPreview();
  }}

  function escapeHtml(s) {{
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }}

  function collectTags() {{
    var out = [];
    tagTable.querySelectorAll("tbody.train-pair tr.human").forEach(function (tr) {{
      out.push({{
        tag: tr.dataset.tag,
        human_checked: tr.querySelector(".hum-check").checked,
        human_keywords: tr.querySelector(".hum-kw").value,
        human_include_guards: tr.querySelector(".hum-inc").value,
        human_exclude_guards: tr.querySelector(".hum-exc").value
      }});
    }});
    return out;
  }}

  function splitCsv(raw) {{
    var out = [];
    var seen = Object.create(null);
    String(raw || "").split(",").forEach(function (p) {{
      var t = p.trim();
      if (!t) return;
      var k = t.toLowerCase();
      if (seen[k]) return;
      seen[k] = true;
      out.push(t);
    }});
    return out;
  }}

  function updateGuardPreview() {{
    if (!guardPreviewEl) return;
    var lines = [];
    tagTable.querySelectorAll("tbody.train-pair tr.human").forEach(function (tr) {{
      var tag = tr.dataset.tag || "";
      var existingInc = splitCsv(tr.dataset.modelInclude || "");
      var existingExc = splitCsv(tr.dataset.modelExclude || "");
      var addInc = splitCsv(tr.querySelector(".hum-inc").value);
      var addExc = splitCsv(tr.querySelector(".hum-exc").value);
      var existingIncSet = Object.create(null);
      var existingExcSet = Object.create(null);
      existingInc.forEach(function (t) {{ existingIncSet[t.toLowerCase()] = true; }});
      existingExc.forEach(function (t) {{ existingExcSet[t.toLowerCase()] = true; }});
      var newInc = addInc.filter(function (t) {{ return !existingIncSet[t.toLowerCase()]; }});
      var newExc = addExc.filter(function (t) {{ return !existingExcSet[t.toLowerCase()]; }});
      if (newInc.length || newExc.length) {{
        var parts = [];
        if (newInc.length) parts.push("+" + newInc.join(", +"));
        if (newExc.length) parts.push("-" + newExc.join(", -"));
        lines.push(tag + ": " + parts.join("  "));
      }}
    }});
    guardPreviewEl.textContent = lines.length
      ? ("Pending guard changes on Apply:\\n" + lines.join("\\n"))
      : "Pending guard changes on Apply: (none)";
  }}

  async function load() {{
    applyBtn.disabled = true;
    try {{
      var r = await fetch("/api/message/" + ROWID);
      var data = await r.json();
      if (!r.ok) throw new Error(data.error || r.statusText);
      render(data);
    }} catch (e) {{
      showErr(String(e));
    }} finally {{
      applyBtn.disabled = false;
    }}
  }}

  document.getElementById("done").addEventListener("click", function () {{
    closeTrainingTab();
  }});

  applyBtn.addEventListener("click", async function () {{
    showErr("");
    applyBtn.disabled = true;
    try {{
      var r = await fetch("/api/message/" + ROWID + "/regenerate", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ tags: collectTags(), author: REVIEWER_ID || "unknown" }})
      }});
      var data = await r.json();
      if (!r.ok) throw new Error(data.error || r.statusText);
      render(data.state);
      closeTrainingTab();
    }} catch (e) {{
      showErr(String(e));
      applyBtn.disabled = false;
    }}
  }});

  load();
}})();
  </script>
</body>
</html>
"""


def _api_tag_catalog_mutate(conn: sqlite3.Connection, body: dict[str, object]) -> None:
    """Apply one mutation and commit. Raises ValueError on bad input."""
    op = str(body.get("op") or "").strip().lower()
    att.ensure_training_tables(conn)
    if op == "add":
        tag = str(body.get("tag") or "").strip()
        active = bool(body.get("active", True))
        archive_enabled = bool(body.get("archive_enabled", False))
        tag_catalog.upsert_tag_row(conn, tag, active=active, archive_enabled=archive_enabled)
        conn.commit()
        return
    if op == "update":
        tag = str(body.get("tag") or "").strip()
        active_v = body.get("active")
        arch_v = body.get("archive_enabled")
        active = active_v if isinstance(active_v, bool) else None
        archive_enabled = arch_v if isinstance(arch_v, bool) else None
        if active is None and archive_enabled is None:
            raise ValueError("update requires at least one of active, archive_enabled (boolean)")
        tag_catalog.set_tag_flags(
            conn, tag, active=active, archive_enabled=archive_enabled
        )
        conn.commit()
        return
    if op == "merge":
        fr = str(body.get("from") or body.get("source") or "").strip()
        into = str(body.get("into") or body.get("target") or "").strip()
        if not fr or not into:
            raise ValueError("merge requires from and into (existing target tag)")
        att.merge_classifier_tag_into(conn, fr, into)
        conn.commit()
        return
    raise ValueError(f"unknown op {op!r} (use add, update, merge)")


_TAG_CATALOG_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>sms-ripper — Tag catalog</title>
  <style>
__THEME_CSS__
__TOGGLE_CSS__
    :root { font-family: system-ui, sans-serif; }
    * { box-sizing: border-box; }
    html { background: var(--sr-bg-page); color: var(--sr-fg); }
    body {
      max-width: 900px;
      margin: 2rem auto;
      padding: 0 1rem 5.5rem;
      line-height: 1.45;
      font-size: 0.9rem;
    }
__HERO_H1_CSS__
    h1 { font-size: 1.35rem; }
    nav { margin-bottom: 1rem; font-size: 0.9rem; }
    nav a { color: var(--sr-link); }
    nav a:visited { color: var(--sr-link-visited); }
    .catalog-meta { color: var(--sr-fg-muted); font-size: 0.85rem; margin: 0.5rem 0 1rem; }
    .catalog-meta code { font-size: 0.82em; color: var(--sr-fg); }
    .catalog-blurb { font-size: 0.85rem; color: var(--sr-fg-muted); margin-bottom: 1rem; }
    .catalog-blurb strong { color: var(--sr-fg); }
    table.catalog-tbl { border-collapse: collapse; width: 100%; font-size: 0.85rem; }
    .catalog-tbl th, .catalog-tbl td {
      border: 1px solid var(--sr-border);
      padding: 0.4rem 0.5rem;
      text-align: left;
      vertical-align: middle;
    }
    .catalog-tbl th {
      background: var(--sr-th-bg);
      color: var(--sr-fg-muted);
      font-weight: 600;
      font-size: 0.78rem;
    }
    .catalog-tbl tr:nth-child(even) td { background: var(--sr-tr-alt); }
    td.mono { font-family: ui-monospace, monospace; }
    .catalog-tbl input[type="checkbox"] { accent-color: var(--sr-link); vertical-align: middle; }
    .catalog-tbl input[type="text"] {
      padding: 0.25rem 0.4rem;
      width: 100%;
      max-width: 14rem;
      background: var(--sr-bg-page);
      color: var(--sr-fg);
      border: 1px solid var(--sr-border);
      border-radius: 4px;
    }
    .catalog-tbl button {
      font: inherit;
      padding: 0.28rem 0.65rem;
      cursor: pointer;
      background: var(--sr-bar-btn-bg);
      color: var(--sr-bar-btn-fg);
      border: 1px solid var(--sr-bar-btn-border);
      border-radius: 4px;
    }
    .catalog-tbl button:hover {
      color: var(--sr-bar-btn-hover-fg);
      border-color: var(--sr-bar-btn-hover-border);
    }
    .err { color: var(--sr-err); margin: 0.5rem 0; }
    .ok { color: var(--sr-badge-ok); margin: 0.5rem 0; font-size: 0.85rem; }
    footer.add-row {
      margin-top: 1.5rem;
      padding-top: 1rem;
      border-top: 1px solid var(--sr-border);
    }
    footer.add-row label { color: var(--sr-fg-muted); }
    footer.add-row input[type="text"] {
      padding: 0.28rem 0.45rem;
      background: var(--sr-bg-page);
      color: var(--sr-fg);
      border: 1px solid var(--sr-border);
      border-radius: 4px;
      width: 12rem;
    }
    footer.add-row button {
      font: inherit;
      padding: 0.28rem 0.65rem;
      cursor: pointer;
      background: var(--sr-bar-btn-bg);
      color: var(--sr-bar-btn-fg);
      border: 1px solid var(--sr-bar-btn-border);
      border-radius: 4px;
    }
    label { display: inline-block; margin-right: 0.75rem; font-size: 0.85rem; }
  </style>
__THEME_BOOTSTRAP_HEAD__
</head>
<body>
__TOGGLE_HTML__
  <nav><a href="/">← Archive index</a></nav>
  <h1>Tag catalog</h1>
  <p class="catalog-meta">Page served: <span class="dt-adjustable" data-utc="__PAGE_UTC__">…</span>
  (use <strong>Time</strong> above for UTC vs local — matches archive index &amp; training pages.)</p>
  <p class="catalog-blurb">Active tags drive the classifier prompt and training UI.
  <strong>Archive</strong> marks tags that copy rows into <code>message_tags_archive</code> when present
  (first match in attribute order). <strong>Merge into</strong> folds the source tag into an <em>existing</em> tag: rewrites <code>classifier_attributes</code> on archive tables, merges training rows and guards, then removes the source from the catalog. To use a <em>new</em> key, add that tag first, then merge the old tag into it.</p>
  <p class="err" id="msg" hidden></p>
  <p class="ok" id="ok" hidden></p>
  <div id="tbl"></div>
  <footer class="add-row">
    <strong>Add tag</strong>
    <div style="margin-top:0.5rem">
      <input type="text" id="newtag" placeholder="e.g. finance" />
      <label><input type="checkbox" id="newactive" checked /> active</label>
      <label><input type="checkbox" id="newarch" /> archive</label>
      <button type="button" id="btnadd">Add</button>
    </div>
  </footer>
  <script>
(function () {
  var msgEl = document.getElementById("msg");
  var okEl = document.getElementById("ok");
  var tblEl = document.getElementById("tbl");
  function showErr(t) {
    msgEl.textContent = t || "";
    msgEl.hidden = !t;
    okEl.hidden = true;
  }
  function showOk(t) {
    okEl.textContent = t || "Saved.";
    okEl.hidden = false;
    msgEl.hidden = true;
  }
  function esc(s) {
    return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
  }
  function render(rows) {
    var h = "<table class=\\"catalog-tbl\\"><thead><tr><th>Tag</th><th>Active</th><th>Archive</th><th>Merge into</th><th></th></tr></thead><tbody>";
    rows.forEach(function (r) {
      var tag = r.tag;
      h += "<tr><td class=\\"mono\\">" + esc(tag) + "</td>";
      h += "<td><input type=\\"checkbox\\" data-tag=\\"" + esc(tag) + "\\" class=\\"cb-act\\" " + (r.active ? "checked" : "") + " /></td>";
      h += "<td><input type=\\"checkbox\\" data-tag=\\"" + esc(tag) + "\\" class=\\"cb-arch\\" " + (r.archive_enabled ? "checked" : "") + " /></td>";
      h += "<td><input type=\\"text\\" class=\\"mg-into\\" data-tag=\\"" + esc(tag) + "\\" placeholder=\\"existing tag\\" /></td>";
      h += "<td><button type=\\"button\\" class=\\"btn-mg\\" data-tag=\\"" + esc(tag) + "\\">Merge</button></td></tr>";
    });
    h += "</tbody></table>";
    tblEl.innerHTML = h;
    tblEl.querySelectorAll(".cb-act").forEach(function (el) {
      el.addEventListener("change", function () {
        var t = el.getAttribute("data-tag");
        post({ op: "update", tag: t, active: el.checked });
      });
    });
    tblEl.querySelectorAll(".cb-arch").forEach(function (el) {
      el.addEventListener("change", function () {
        var t = el.getAttribute("data-tag");
        post({ op: "update", tag: t, archive_enabled: el.checked });
      });
    });
    tblEl.querySelectorAll(".btn-mg").forEach(function (el) {
      el.addEventListener("click", function () {
        var t = el.getAttribute("data-tag");
        var tr = el.closest("tr");
        var inp = tr ? tr.querySelector(".mg-into") : null;
        var into = (inp && inp.value || "").trim();
        if (!into) { showErr("Enter existing tag to merge into"); return; }
        if (into === t) { showErr("Cannot merge a tag into itself"); return; }
        post({ op: "merge", from: t, into: into }).then(function () { if (inp) inp.value = ""; });
      });
    });
  }
  function load() {
    showErr("");
    return fetch("/api/tag-catalog").then(function (r) { return r.json(); }).then(function (d) {
      if (d.error) showErr(d.error);
      else render(d.tags || []);
    }).catch(function (e) { showErr(String(e)); });
  }
  function post(body) {
    showErr("");
    return fetch("/api/tag-catalog", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().then(function (j) { return { ok: r.ok, j: j }; });
    }).then(function (x) {
      if (!x.ok) {
        showErr((x.j && x.j.error) || "Request failed");
        return Promise.reject(new Error((x.j && x.j.error) || "fail"));
      }
      showOk("Saved.");
      return load();
    });
  }
  document.getElementById("btnadd").addEventListener("click", function () {
    var tag = document.getElementById("newtag").value.trim();
    if (!tag) { showErr("Enter a tag name"); return; }
    post({
      op: "add",
      tag: tag,
      active: document.getElementById("newactive").checked,
      archive_enabled: document.getElementById("newarch").checked
    }).then(function () { document.getElementById("newtag").value = ""; });
  });
  load();
})();
  </script>
__THEME_JS__
__TOGGLE_JS__
</body>
</html>
"""


def _tag_catalog_html() -> str:
    """Full tag-catalog page HTML with theme/tz controls (shared with index via html_tz_toggle)."""
    ht = html_tz_toggle
    page_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    return (
        _TAG_CATALOG_TEMPLATE.replace("__THEME_CSS__", ht.THEME_CSS)
        .replace("__TOGGLE_CSS__", ht.TOGGLE_CSS)
        .replace("__HERO_H1_CSS__", ht.HERO_H1_CSS)
        .replace("__THEME_BOOTSTRAP_HEAD__", ht.THEME_BOOTSTRAP_HEAD)
        .replace("__TOGGLE_HTML__", ht.TOGGLE_HTML)
        .replace("__THEME_JS__", ht.THEME_JS)
        .replace("__TOGGLE_JS__", ht.TOGGLE_JS)
        .replace("__PAGE_UTC__", page_utc)
    )


class TrainingHandler(BaseHTTPRequestHandler):
    server_version = "sms-ripper-training/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("%s - - [%s] %s\n" % (self.client_address[0], self.log_date_time_string(), fmt % args))

    def log_request(self, code: object = "-", size: object = "-") -> None:
        """Skip noise (Chrome DevTools probes /.well-known/..., favicon)."""
        path = getattr(self, "path", "") or ""
        line = getattr(self, "requestline", "") or ""
        if "/.well-known/" in path or "/.well-known/" in line:
            return
        if path.rstrip("/").endswith("favicon.ico") or " favicon.ico " in line:
            return
        super().log_request(code, size)

    def _db(self) -> sqlite3.Connection:
        return self.server.db_conn_factory()  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/.well-known/"):
            self.send_response(204)
            self.end_headers()
            return
        path = parsed.path.strip("/")
        parts = path.split("/") if path else []

        if len(parts) == 1 and parts[0] == "favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        if len(parts) == 1 and parts[0] == "CHANGELOG.md":
            chlog = _REPO / "CHANGELOG.md"
            if not chlog.is_file():
                self.send_error(404)
                return
            raw = chlog.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        if not parts:
            conn = self._db()
            try:
                doc = report_html.build_training_server_index_html(
                    conn,
                    limit=int(self.server.index_row_limit),  # type: ignore[attr-defined]
                    db_path=str(self.server.chat_db_display_path),  # type: ignore[attr-defined]
                )
            finally:
                conn.close()
            body = doc.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if len(parts) == 2 and parts[0] == "message" and parts[1].isdigit():
            rid = int(parts[1])
            reviewer_id = str(getattr(self.server, "reviewer_id", "") or "")
            body = _page_html(rid, reviewer_id=reviewer_id).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # Allow cycle links in the index page to work on the same origin.
        # The index report links to: daemon-cycles/cycle_....html (relative).
        if len(parts) == 2 and parts[0] == "daemon-cycles":
            filename = parts[1]
            if "/" in filename or "\\" in filename or not filename.endswith(".html"):
                self.send_error(404)
                return
            fpath = _DAEMON_CYCLES_DIR / filename
            if not fpath.is_file():
                self.send_error(404)
                return
            body = fpath.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if len(parts) == 3 and parts[0] == "api" and parts[1] == "message" and parts[2].isdigit():
            rid = int(parts[2])
            conn = self._db()
            try:
                row = _fetch_archive_message(conn, rid)
                if not row:
                    st, b, ct = _json_bytes({"error": "not found"}, 404)
                else:
                    st, b, ct = _json_bytes(
                        att.build_message_state(
                            conn,
                            archive_rowid=rid,
                            text=att.coerce_str_field(row["text"]),
                            subject=row["subject"],
                            handle=row["handle"],
                            date_ns=att.coerce_apple_timestamp_ns(row["date_ns"]),
                            classifier_attributes_raw=row["classifier_attributes"],
                            generated_at_iso=att.utc_now_iso(),
                        )
                    )
            except Exception as e:
                st, b, ct = _json_bytes(
                    {"error": str(e), "trace": traceback.format_exc()},
                    500,
                )
            finally:
                conn.close()
            self.send_response(st)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return

        if len(parts) == 2 and parts[0] == "api" and parts[1] == "tag-catalog":
            conn = self._db()
            try:
                rows = tag_catalog.list_catalog_rows(conn)
                st, b, ct = _json_bytes({"tags": rows}, 200)
            except Exception as e:
                st, b, ct = _json_bytes(
                    {"error": str(e), "trace": traceback.format_exc()},
                    500,
                )
            finally:
                conn.close()
            self.send_response(st)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return

        if len(parts) == 1 and parts[0] == "tag-catalog":
            raw = _tag_catalog_html().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        self.send_error(404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.strip("/")
        parts = path.split("/") if path else []

        if (
            len(parts) == 4
            and parts[0] == "api"
            and parts[1] == "message"
            and parts[2].isdigit()
            and parts[3] == "regenerate"
        ):
            rid = int(parts[2])
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                body = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                st, b, ct = _json_bytes({"error": "invalid JSON"}, 400)
                self.send_response(st)
                self.send_header("Content-Type", ct)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
                return
            tags = body.get("tags")
            if not isinstance(tags, list):
                st, b, ct = _json_bytes({"error": "missing tags array"}, 400)
                self.send_response(st)
                self.send_header("Content-Type", ct)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
                return
            author = body.get("author")
            author_s = str(author).strip() if author is not None else "unknown"
            if not author_s:
                author_s = "unknown"
            got = {
                tag_catalog.normalize_tag(str(t.get("tag", "")))
                for t in tags
                if isinstance(t, dict) and tag_catalog.normalize_tag(str(t.get("tag", "")))
            }
            conn = self._db()
            try:
                expected = _training_tags_set(conn)
                if got != expected:
                    st, b, ct = _json_bytes(
                        {
                            "error": "tags must include exactly the active catalog tag set",
                            "expected": sorted(expected),
                        },
                        400,
                    )
                    self.send_response(st)
                    self.send_header("Content-Type", ct)
                    self.send_header("Content-Length", str(len(b)))
                    self.end_headers()
                    self.wfile.write(b)
                    return
                row = _fetch_archive_message(conn, rid)
                if not row:
                    st, b, ct = _json_bytes({"error": "not found"}, 404)
                else:
                    msg_text = att.coerce_str_field(row["text"])
                    attrs, reason = att.apply_regenerate(
                        conn,
                        archive_rowid=rid,
                        message_text=msg_text,
                        message_subject=att.coerce_str_field(row["subject"]),
                        human_tag_rows=[t for t in tags if isinstance(t, dict)],
                        author=author_s,
                    )
                    state = att.build_message_state(
                        conn,
                        archive_rowid=rid,
                        text=msg_text,
                        subject=row["subject"],
                        handle=row["handle"],
                        date_ns=att.coerce_apple_timestamp_ns(row["date_ns"]),
                        classifier_attributes_raw=json.dumps(attrs),
                        generated_at_iso=att.utc_now_iso(),
                    )
                    st, b, ct = _json_bytes({"ok": True, "attributes": attrs, "reason": reason, "state": state})
            except Exception as e:
                st, b, ct = _json_bytes(
                    {"error": str(e), "trace": traceback.format_exc()},
                    500,
                )
            finally:
                conn.close()
            self.send_response(st)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return

        if len(parts) == 2 and parts[0] == "api" and parts[1] == "tag-catalog":
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                body = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                st, b, ct = _json_bytes({"error": "invalid JSON"}, 400)
                self.send_response(st)
                self.send_header("Content-Type", ct)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
                return
            if not isinstance(body, dict):
                st, b, ct = _json_bytes({"error": "JSON body must be an object"}, 400)
                self.send_response(st)
                self.send_header("Content-Type", ct)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
                return
            conn = self._db()
            try:
                _api_tag_catalog_mutate(conn, body)
                st, b, ct = _json_bytes({"ok": True}, 200)
            except ValueError as e:
                conn.rollback()
                st, b, ct = _json_bytes({"error": str(e)}, 400)
            except Exception as e:
                conn.rollback()
                st, b, ct = _json_bytes(
                    {"error": str(e), "trace": traceback.format_exc()},
                    500,
                )
            finally:
                conn.close()
            self.send_response(st)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return

        self.send_error(404)


def _open_browser_chrome_or_default(url: str) -> None:
    """macOS: prefer Google Chrome; otherwise fall back to default browser."""
    try:
        if sys.platform == "darwin":
            r = subprocess.run(
                ["open", "-a", "Google Chrome", url],
                check=False,
                capture_output=True,
                timeout=15,
            )
            if r.returncode != 0:
                subprocess.run(
                    ["open", url],
                    check=False,
                    capture_output=True,
                    timeout=15,
                )
        else:
            import webbrowser

            webbrowser.open(url)
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Local archive tag training server (loopback only)")
    parser.add_argument("--chat-db", type=Path, default=None, help="Path to chat.db")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default loopback)")
    parser.add_argument("--port", type=int, default=8765, help="TCP port")
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Max rows on GET / (same table as reports/index.html)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open a browser (Chrome on macOS, default browser elsewhere)",
    )
    args = parser.parse_args()
    db_path = Path(args.chat_db or config.CHAT_DB_PATH).expanduser()
    if not db_path.is_file():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 1

    uri = f"file:{db_path}?mode=rwc"

    def open_conn() -> sqlite3.Connection:
        conn = sqlite3.connect(uri, uri=True, timeout=60.0)
        archive._register_chat_db_trigger_stubs(conn)
        return conn

    httpd = ThreadingHTTPServer((args.host, args.port), TrainingHandler)
    httpd.db_conn_factory = open_conn  # type: ignore[attr-defined]
    httpd.index_row_limit = max(1, args.limit)  # type: ignore[attr-defined]
    httpd.chat_db_display_path = db_path  # type: ignore[attr-defined]
    httpd.reviewer_id = str(getattr(config, "REVIEWER_ID", "") or "").strip()  # type: ignore[attr-defined]
    base_url = f"http://{args.host}:{args.port}/"
    print(f"Archive training server {base_url} (database {db_path})")
    print(
        f"GET / — archive index (up to {args.limit} rows); full opens /message/<rowid>; "
        "GET /tag-catalog — edit tag list (stored in chat.db)."
    )
    if not args.no_browser:
        _open_browser_chrome_or_default(base_url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
