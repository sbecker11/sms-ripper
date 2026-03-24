"""Smoke test for scripts/dry_run_recent.py (subprocess, temp chat.db)."""

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import reader
from tests.conftest import populate_chat_db


def test_preview_recent_offline_outputs_actions(chat_db_path):
    root = Path(__file__).resolve().parent.parent
    now_ns = reader.datetime_to_apple_ts(datetime.utcnow())
    populate_chat_db(chat_db_path, text="hello preview", date_ns=now_ns)

    env = {**os.environ, "CHAT_DB_PATH": str(chat_db_path)}
    r = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "dry_run_recent.py"),
            "--no-classify",
            "--limit",
            "3",
        ],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 0, r.stderr
    assert "Dry-run preview" in r.stdout
    assert "1/1 " in r.stdout
    assert "Actions (execution):" in r.stdout
    assert "chat.db (SQLite):" in r.stdout
    assert "Matched rules:" in r.stdout


def test_preview_recent_compact_offline(chat_db_path):
    root = Path(__file__).resolve().parent.parent
    now_ns = reader.datetime_to_apple_ts(datetime.utcnow())
    populate_chat_db(chat_db_path, text="compact line", date_ns=now_ns)

    env = {**os.environ, "CHAT_DB_PATH": str(chat_db_path)}
    r = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "dry_run_recent.py"),
            "--compact",
            "--no-classify",
            "--limit",
            "2",
        ],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 0, r.stderr
    assert "--- 1/1 |" in r.stdout
    assert "Attributes:" in r.stdout
    assert "Matched rules:" in r.stdout
    assert "Actions (execution):" in r.stdout
    assert "Dry-run preview" not in r.stdout
