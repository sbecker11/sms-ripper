"""Tests for macOS FDA path helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from macos_fda_paths import (  # noqa: E402
    fda_absolute_paths,
    framework_python_app_executable,
    resolved_venv_python,
)


def test_resolved_venv_python_missing_venv(tmp_path: Path):
    assert resolved_venv_python(tmp_path) is None


def test_fda_absolute_paths_empty_without_venv(tmp_path: Path):
    assert fda_absolute_paths(tmp_path) == []


@pytest.mark.skipif(
    not Path("/usr/local/Cellar").is_dir(), reason="Homebrew Cellar layout not present"
)
def test_framework_python_app_when_venv_exists():
    repo = Path(__file__).resolve().parent.parent
    vpy = repo / "venv" / "bin" / "python"
    if not vpy.is_file():
        pytest.skip("no project venv")
    app = framework_python_app_executable(repo)
    assert app
    assert "Python.app" in app
    assert Path(app).is_file()
