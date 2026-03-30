#!/usr/bin/env python3
"""
Build reports/daemon-cycles/index.html plus one HTML page per parsed daemon cycle.

Expects cycle markers (see daemon_cycle.py):
  {ts} ======== cycle start pid=<PID> ========
  {ts} ======== cycle end pid=<PID> OK ========
  {ts} ======== cycle end pid=<PID> ERROR after <step> ========

Run after each daemon cycle (or manually: poe daemon-cycles-generate).
"""

from __future__ import annotations

import argparse
import html
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from daemon_log_cycles import DaemonCycle, parse_daemon_log_text  # noqa: E402
import html_tz_toggle  # noqa: E402

DEFAULT_LOG = _REPO / "logs" / "daemon.log"
DEFAULT_OUT = _REPO / "reports" / "daemon-cycles"
# Index lists at most this many cycles (newest first); older cycles stay in daemon.log only.
DEFAULT_MAX_CYCLES = 50

# Inline SVG — “list” glyph for return to daemon-cycles index (file:// safe).
_SVG_INDEX_NAV = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M2 2.5a.5.5 0 0 1 .5-.5h11a.5.5 0 0 1 0 1h-11a.5.5 0 0 1-.5-.5zm0 4a.5.5 0 0 1 .5-.5h7a.5.5 0 0 1 0 1h-7a.5.5 0 0 1-.5-.5zm0 4a.5.5 0 0 1 .5-.5h11a.5.5 0 0 1 0 1h-11a.5.5 0 0 1-.5-.5zm0 4a.5.5 0 0 1 .5-.5h7a.5.5 0 0 1 0 1h-7a.5.5 0 0 1-.5-.5z"/></svg>"""

_CSS = """
:root { font-family: system-ui, sans-serif; }
html { background: var(--sr-bg-page); color: var(--sr-fg); }
body { max-width: 1100px; margin: 2rem auto; padding: 0 1rem; }
a { color: var(--sr-link); }
a:visited { color: var(--sr-link-visited); }
a:hover { color: var(--sr-link-hover); }
h1 { font-size: 1.25rem; }
.meta { color: var(--sr-fg-muted); font-size: 0.9rem; margin-bottom: 1rem; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { border: 1px solid var(--sr-border); padding: 0.5rem 0.6rem; text-align: left; }
th { background: var(--sr-th-bg); }
tr:nth-child(even) { background: var(--sr-tr-alt); }
.badge-ok { color: var(--sr-badge-ok); }
.badge-err { color: var(--sr-badge-err); }
.badge-inc { color: var(--sr-badge-inc); }
pre { background: var(--sr-pre-bg); border: 1px solid var(--sr-border); padding: 1rem; overflow-x: auto; font-size: 0.8rem; white-space: pre-wrap; word-break: break-word; }
.cycle-times { margin-bottom: 0.75rem; }
.cycle-times p.meta { margin: 0.25rem 0; }
.hint { background: var(--sr-hint-bg); border: 1px solid var(--sr-hint-border); padding: 0.75rem 1rem; margin-top: 1.5rem; font-size: 0.85rem; }
code { font-size: 0.9em; }
a.icon-nav { color: var(--sr-link); text-decoration: none; display: inline-flex; align-items: center; }
a.icon-nav:hover { color: var(--sr-link-hover); }
a.icon-nav svg { display: block; }
td.ts-dual { font-size: 0.82rem; line-height: 1.35; vertical-align: top; word-break: break-word; }
"""


def _parse_cycle_utc_ts(ts: str | None) -> datetime | None:
    """Parse daemon log UTC timestamp ``YYYY-MM-DDTHH:MM:SSZ``."""
    if not ts or not str(ts).strip() or str(ts).strip() == "—":
        return None
    s = str(ts).strip()
    if not s.endswith("Z"):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _fmt_utc_html(dt: datetime) -> str:
    u = dt.astimezone(timezone.utc)
    d = html.escape(u.strftime("%Y-%m-%d"))
    t = html.escape(u.strftime("%H:%M:%S UTC"))
    return f"{d}<br/>{t}"


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z"


def _cycle_times_header(start_ts: str, end_ts: str | None) -> str:
    """Start/end with data-utc for the browser timezone toggle (default text is UTC)."""

    def _line(label: str, raw: str | None, dt: datetime | None) -> str:
        if dt:
            iso = _iso_z(dt)
            inner = (
                f'<span class="dt-adjustable" data-utc="{html.escape(iso)}">'
                f"{_fmt_utc_html(dt)}</span>"
            )
        else:
            inner = html.escape((raw or "—").strip())
        return f'<p class="meta"><strong>{label}</strong> · {inner}</p>'

    s_dt = _parse_cycle_utc_ts(start_ts)
    e_dt = _parse_cycle_utc_ts(end_ts) if end_ts else None
    lines = [_line("start", start_ts, s_dt)]
    if end_ts and str(end_ts).strip() not in ("", "—"):
        lines.append(_line("end", end_ts, e_dt))
    else:
        lines.append('<p class="meta"><strong>end</strong> · —</p>')
    return '<div class="cycle-times">\n' + "\n".join(lines) + "\n</div>"


def _index_dual_time_cell(ts: str | None, *, href: str | None = None) -> str:
    """Index cell: one adjustable instant (UTC by default); optional link on start column."""
    if not ts or str(ts).strip() in ("", "—"):
        return '<td class="ts-dual col-datetime">—</td>'
    dt = _parse_cycle_utc_ts(ts)
    if dt:
        iso = _iso_z(dt)
        inner = (
            f'<span class="dt-adjustable" data-utc="{html.escape(iso)}">'
            f"{_fmt_utc_html(dt)}</span>"
        )
    else:
        inner = html.escape(str(ts).strip())
    if href:
        inner = f'<a href="{html.escape(href)}">{inner}</a>'
    return f'<td class="ts-dual col-datetime">{inner}</td>'


def _shell_page(
    title: str,
    body_inner: str,
    back_href: str | None = "index.html",
    *,
    back_kind: str | None = "text",
) -> str:
    """back_kind: None or missing back_href → no back link; 'text' → ← Index; 'icon' → list SVG."""
    back = ""
    if back_href and back_kind == "text":
        back = f'<p class="meta"><a href="{html.escape(back_href)}">← Index</a></p>'
    elif back_href and back_kind == "icon":
        lab = "Daemon cycles index"
        back = (
            f'<p class="meta"><a href="{html.escape(back_href)}" class="icon-nav" '
            f'title="{html.escape(lab)}" aria-label="{html.escape(lab)}">{_SVG_INDEX_NAV}</a></p>'
        )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(title)}</title>
  <style>{html_tz_toggle.THEME_CSS}</style>
  <style>{_CSS}</style>
  <style>{html_tz_toggle.TOGGLE_CSS}</style>
{html_tz_toggle.THEME_BOOTSTRAP_HEAD}
</head>
<body>
{html_tz_toggle.TOGGLE_HTML}
{back}
{body_inner}
{html_tz_toggle.THEME_JS}
{html_tz_toggle.TOGGLE_JS}
</body>
</html>
"""


def _write_cycle_page(out_dir: Path, c: DaemonCycle, *, on_index: bool) -> str:
    fname = f"cycle_{c.file_slug()}.html"
    path = out_dir / fname
    body = f"""<h1>Daemon cycle</h1>
{_cycle_times_header(c.start_ts, c.end_ts)}
<p class="meta">pid <code>{html.escape(c.pid)}</code> · <span class="badge-{c.status[:3] if c.status != 'incomplete' else 'inc'}">{html.escape(c.status)}</span>
{f" · failed step <code>{html.escape(c.error_step)}</code>" if c.error_step else ""}</p>
<pre>{html.escape("".join(c.lines))}</pre>
"""
    if on_index:
        shell = _shell_page(
            f"cycle {c.pid} {c.start_ts}",
            body,
            back_href="index.html",
            back_kind="icon",
        )
    else:
        shell = _shell_page(f"cycle {c.pid} {c.start_ts}", body, back_href=None, back_kind=None)
    path.write_text(shell, encoding="utf-8")
    return fname


def _write_index(
    out_dir: Path,
    ordered_newest_first: list[DaemonCycle],
    total_parsed: int,
    log_path: Path,
    max_cycles: int,
) -> None:
    shown = ordered_newest_first[:max_cycles]

    rows = []
    for c in shown:
        fn = f"cycle_{c.file_slug()}.html"
        st_class = (
            "badge-ok"
            if c.status == "ok"
            else "badge-err"
            if c.status == "error"
            else "badge-inc"
        )
        rows.append(
            "<tr>"
            f"{_index_dual_time_cell(c.start_ts, href=fn)}"
            f"{_index_dual_time_cell(c.end_ts)}"
            f"<td><code>{html.escape(c.pid)}</code></td>"
            f"<td><span class=\"{st_class}\">{html.escape(c.status)}</span></td>"
            f"<td>{html.escape(c.error_step or '—')}</td>"
            "</tr>"
        )
    rows_html = "\n".join(rows) if rows else "<tr><td colspan=\"5\">(no cycles parsed yet)</td></tr>"

    omitted = total_parsed - len(shown)
    omitted_note = ""
    if omitted > 0:
        omitted_note = (
            f"<p class=\"meta\">{omitted} older cycle(s) are in the log but not listed here "
            f"(index limit <code>max_cycles={max_cycles}</code>).</p>"
        )

    body = f"""<h1>Daemon cycles</h1>
<p class="meta">Parsed from <code>{html.escape(str(log_path))}</code> · {total_parsed} cycle(s) in log · showing <strong>{len(shown)}</strong> newest (cap {max_cycles})</p>
{omitted_note}
<h2>Recent cycles (newest first)</h2>
<table>
<thead><tr><th class="col-datetime" title="Instant in UTC; use UTC / Local buttons">start</th><th class="col-datetime" title="Same">end</th><th>pid</th><th>status</th><th>error step</th></tr></thead>
<tbody>
{rows_html}
</tbody>
</table>
<div class="hint">
Open via <code>file://</code> or <code>poe daemon-cycles-open</code>. Regenerated after each daemon cycle.
Change the cap with <code>--max-cycles N</code> (default {DEFAULT_MAX_CYCLES}).
Use <strong>Theme</strong> (top right) for light or dark colors (<code>localStorage</code> <code>smsRipperTheme</code>).
Use <strong>UTC</strong> / <strong>Local</strong> for time display (<code>cookie</code> <code>smsRipperTzDisplay</code>).
<br /><br />
<a href="../index.html">Political archive report</a> — companion static page under <code>reports/</code>.
</div>
"""
    (out_dir / "index.html").write_text(
        _shell_page("sms-ripper — daemon cycles", body, back_href=None, back_kind=None),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate daemon cycle index + per-cycle HTML")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=DEFAULT_MAX_CYCLES,
        metavar="N",
        help=f"Max recent cycles in index and on disk (default {DEFAULT_MAX_CYCLES})",
    )
    args = parser.parse_args()
    if args.max_cycles < 1:
        print("--max-cycles must be >= 1", file=sys.stderr)
        return 1
    log_path = args.log.expanduser()
    out_dir = args.out_dir.expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    if not log_path.is_file():
        out_dir.mkdir(parents=True, exist_ok=True)
        stub = _shell_page(
            "sms-ripper — daemon cycles",
            f"""<p>No log file yet: <code>{html.escape(str(log_path))}</code></p>
<p class="meta"><a href="../index.html">← Political archive report</a></p>
<div class="hint">After the first daemon cycle, this page lists recent runs. Regenerated automatically at cycle end.</div>""",
            back_href=None,
            back_kind=None,
        )
        (out_dir / "index.html").write_text(stub, encoding="utf-8")
        print(f"No log at {log_path}; wrote stub index.html only")
        return 0

    text = log_path.read_text(encoding="utf-8", errors="replace")
    cycles = parse_daemon_log_text(text)
    ordered = sorted(cycles, key=lambda x: x.start_ts, reverse=True)
    recent = ordered[: args.max_cycles]
    indexed_slugs = {c.file_slug() for c in recent}

    for old in out_dir.glob("cycle_*.html"):
        try:
            old.unlink()
        except OSError:
            pass

    for c in recent:
        _write_cycle_page(out_dir, c, on_index=c.file_slug() in indexed_slugs)

    _write_index(out_dir, ordered, len(cycles), log_path, args.max_cycles)
    print(
        f"Wrote index + {len(recent)} cycle page(s) (max_cycles={args.max_cycles}, "
        f"{len(cycles)} parsed) under {out_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
