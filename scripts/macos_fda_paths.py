"""Paths to add for macOS Full Disk Access (Homebrew framework layout)."""

from __future__ import annotations

import os
from pathlib import Path

SQLITE3_SYSTEM = "/usr/bin/sqlite3"
# TCC applies to the process that opens the file; cp is one stable path (unlike brew Python).
CP_SYSTEM = "/bin/cp"


def resolved_venv_python(repo_root: Path) -> str | None:
    p = repo_root / "venv" / "bin" / "python"
    if not p.is_file():
        return None
    return os.path.realpath(str(p))


def framework_python_app_executable(repo_root: Path) -> str | None:
    """
    Homebrew Python.framework often runs from Python.app; TCC may only honor this binary,
    not .../bin/python3.11.
    """
    r = resolved_venv_python(repo_root)
    if not r:
        return None
    ver_root = Path(r).resolve().parent.parent
    app = ver_root / "Resources" / "Python.app" / "Contents" / "MacOS" / "Python"
    if app.is_file():
        return str(app)
    return None


def fda_path_lines(repo_root: Path) -> list[str]:
    """Human-readable lines for `poe daemon-fda-path` and error text."""
    lines: list[str] = []
    app = framework_python_app_executable(repo_root)
    real = resolved_venv_python(repo_root)
    vpy = repo_root / "venv" / "bin" / "python"
    lines.append("Add these to Full Disk Access (System Settings → Privacy & Security → +):")
    lines.append("Use Cmd+Shift+G in the file picker to paste a path.")
    lines.append("")
    n = 1
    lines.append(f"{n}) /bin/cp (system copy — add this FIRST; stable path, survives brew upgrades):")
    lines.append(f"   {CP_SYSTEM}")
    lines.append("")
    n += 1
    if app:
        lines.append(
            f"{n}) Python.framework Python.app (often required; add even if you added bin/python3.11):"
        )
        lines.append(f"   {app}")
        lines.append("")
        n += 1
    if real:
        lines.append(f"{n}) Framework bin interpreter:")
        lines.append(f"   {real}")
        lines.append("")
        n += 1
    lines.append(f"{n}) venv entry (optional; rarely enough alone):")
    lines.append(f"   {vpy}")
    lines.append("")
    n += 1
    lines.append(f"{n}) System sqlite3 (stable path; helps sqlite-based backup):")
    lines.append(f"   {SQLITE3_SYSTEM}")
    return lines


def fda_absolute_paths(repo_root: Path) -> list[str]:
    """Ordered unique paths to add (existence-checked where applicable)."""
    vpy = repo_root / "venv" / "bin" / "python"
    if not vpy.is_file():
        return []
    out: list[str] = []
    seen: set[str] = set()
    for p in (
        CP_SYSTEM,
        framework_python_app_executable(repo_root),
        resolved_venv_python(repo_root),
        str(repo_root / "venv" / "bin" / "python"),
        SQLITE3_SYSTEM,
    ):
        if not p or p in seen:
            continue
        if not Path(p).exists():
            continue
        seen.add(p)
        out.append(p)
    return out
