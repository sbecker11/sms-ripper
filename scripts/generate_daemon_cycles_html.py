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
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from daemon_log_cycles import DaemonCycle, parse_daemon_log_text  # noqa: E402

DEFAULT_LOG = _REPO / "logs" / "daemon.log"
DEFAULT_OUT = _REPO / "reports" / "daemon-cycles"
# Index lists at most this many cycles (newest first); older cycles stay in daemon.log only.
DEFAULT_MAX_CYCLES = 50

# Inline SVG — “list” glyph for return to daemon-cycles index (file:// safe).
_SVG_INDEX_NAV = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M2 2.5a.5.5 0 0 1 .5-.5h11a.5.5 0 0 1 0 1h-11a.5.5 0 0 1-.5-.5zm0 4a.5.5 0 0 1 .5-.5h7a.5.5 0 0 1 0 1h-7a.5.5 0 0 1-.5-.5zm0 4a.5.5 0 0 1 .5-.5h11a.5.5 0 0 1 0 1h-11a.5.5 0 0 1-.5-.5zm0 4a.5.5 0 0 1 .5-.5h7a.5.5 0 0 1 0 1h-7a.5.5 0 0 1-.5-.5z"/></svg>"""

_CSS = """
:root { font-family: system-ui, sans-serif; background: #111; color: #e8e8e8; }
body { max-width: 1100px; margin: 2rem auto; padding: 0 1rem; }
a { color: #8cb4ff; }
a:visited { color: #c4a7e7; }
h1 { font-size: 1.25rem; }
.meta { color: #888; font-size: 0.9rem; margin-bottom: 1rem; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { border: 1px solid #333; padding: 0.5rem 0.6rem; text-align: left; }
th { background: #1e1e1e; }
tr:nth-child(even) { background: #161616; }
.badge-ok { color: #6c6; }
.badge-err { color: #f66; }
.badge-inc { color: #fa0; }
pre { background: #0d0d0d; border: 1px solid #333; padding: 1rem; overflow-x: auto; font-size: 0.8rem; white-space: pre-wrap; word-break: break-word; }
.hint { background: #1a1a2e; border: 1px solid #334; padding: 0.75rem 1rem; margin-top: 1.5rem; font-size: 0.85rem; }
code { font-size: 0.9em; }
a.icon-nav { color: #8cb4ff; text-decoration: none; display: inline-flex; align-items: center; }
a.icon-nav:hover { color: #bcd4ff; }
a.icon-nav svg { display: block; }
"""


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
  <style>{_CSS}</style>
</head>
<body>
{back}
{body_inner}
</body>
</html>
"""


def _write_cycle_page(out_dir: Path, c: DaemonCycle, *, on_index: bool) -> str:
    fname = f"cycle_{c.file_slug()}.html"
    path = out_dir / fname
    body = f"""<h1>Daemon cycle</h1>
<p class="meta">pid <code>{html.escape(c.pid)}</code> · start {html.escape(c.start_ts)}
 · end {html.escape(c.end_ts or "—")} · <span class="badge-{c.status[:3] if c.status != 'incomplete' else 'inc'}">{html.escape(c.status)}</span>
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
            f"<td><a href=\"{html.escape(fn)}\">{html.escape(c.start_ts)}</a></td>"
            f"<td>{html.escape(c.end_ts or '—')}</td>"
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
<thead><tr><th>start</th><th>end</th><th>pid</th><th>status</th><th>error step</th></tr></thead>
<tbody>
{rows_html}
</tbody>
</table>
<div class="hint">
Open via <code>file://</code> or <code>poe daemon-cycles-open</code>. Regenerated after each daemon cycle.
Change the cap with <code>--max-cycles N</code> (default {DEFAULT_MAX_CYCLES}).
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
