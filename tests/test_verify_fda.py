"""Tests for verify_fda probe script."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "scripts" / "verify_fda.py"


@pytest.mark.skipif(not Path("/usr/bin/sqlite3").is_file(), reason="macOS-style sqlite3 path")
def test_verify_fda_passes_on_readable_sqlite_db(tmp_path: Path):
    db = tmp_path / "chat.db"
    subprocess.run(
        ["/usr/bin/sqlite3", str(db), "CREATE TABLE t(i);"],
        check=True,
        capture_output=True,
    )
    r = subprocess.run(
        [sys.executable, str(_SCRIPT), "--chat-db", str(db)],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr + r.stdout
    assert "PASS" in r.stdout
    assert "All probes passed" in r.stdout
