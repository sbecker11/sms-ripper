"""Smoke tests for daemon helper scripts."""

from __future__ import annotations

import importlib.util
import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(
    platform.system() != "Darwin"
    or os.environ.get("CI", "").lower() in ("1", "true", "yes"),
    reason="display alert needs macOS GUI session",
)
def test_daemon_alert_runs_without_crash():
    r = subprocess.run(
        [sys.executable, str(_REPO / "scripts" / "daemon_alert.py"), "test message"],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 0


def test_daemon_cycle_importable():
    path = _REPO / "scripts" / "daemon_cycle.py"
    spec = importlib.util.spec_from_file_location("daemon_cycle", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "main")
