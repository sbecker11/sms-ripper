"""Smoke test for generate_daemon_cycles_html.py."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "scripts" / "generate_daemon_cycles_html.py"


def test_generate_daemon_cycles_writes_index(tmp_path: Path):
    log = tmp_path / "daemon.log"
    out = tmp_path / "out"
    log.write_text(
        "2026-03-29T22:20:01Z ======== cycle start pid=9 ========\n"
        "2026-03-29T22:20:02Z line\n"
        "2026-03-29T22:20:14Z ======== cycle end pid=9 OK ========\n",
        encoding="utf-8",
    )
    r = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--log",
            str(log),
            "--out-dir",
            str(out),
        ],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr + r.stdout
    idx = out / "index.html"
    assert idx.is_file()
    idx_text = idx.read_text(encoding="utf-8")
    assert "Recent cycles (newest first)" in idx_text
    assert "<code>9</code>" in idx_text
    assert "ts-dual" in idx_text
    assert "sms-ripper-tz-bar" in idx_text
    assert "2026-03-29" in idx_text
    assert "22:20:01 UTC" in idx_text
    assert "22:20:14 UTC" in idx_text
    assert "col-datetime" in idx_text
    assert "<br" in idx_text
    cyc = list(out.glob("cycle_*.html"))
    assert len(cyc) == 1
    chtml = cyc[0].read_text(encoding="utf-8")
    assert 'href="index.html"' in chtml
    assert 'class="icon-nav"' in chtml
    assert "Daemon cycles index" in chtml
    assert "← Index" not in chtml
    assert '<div class="cycle-times">' in chtml
    assert "2026-03-29" in chtml
    assert "22:20:01 UTC" in chtml
    assert "22:20:14 UTC" in chtml
    assert "<strong>start</strong>" in chtml and "<strong>end</strong>" in chtml
    assert "sms-ripper-tz-bar" in chtml
    assert "tz-toggle-btn" in chtml
    assert 'data-utc="2026-03-29T22:20:01Z"' in chtml


def test_max_cycles_limits_cycle_files(tmp_path: Path):
    log = tmp_path / "daemon.log"
    out = tmp_path / "out"
    log.write_text(
        "2026-03-28T01:00:00Z ======== cycle start pid=1 ========\n"
        "2026-03-28T01:00:01Z ======== cycle end pid=1 OK ========\n"
        "2026-03-29T02:00:00Z ======== cycle start pid=2 ========\n"
        "2026-03-29T02:00:02Z ======== cycle end pid=2 OK ========\n",
        encoding="utf-8",
    )
    r = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--log",
            str(log),
            "--out-dir",
            str(out),
            "--max-cycles",
            "1",
        ],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr + r.stdout
    assert len(list(out.glob("cycle_*.html"))) == 1
    idx = (out / "index.html").read_text(encoding="utf-8")
    assert "older cycle" in idx
    assert "max_cycles=1" in idx
    assert "<code>2</code>" in idx
    assert "<code>1</code>" not in idx
