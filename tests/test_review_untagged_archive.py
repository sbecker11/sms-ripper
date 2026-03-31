"""Tests for scripts/review_untagged_archive.py."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent


def _load():
    path = _REPO / "scripts" / "review_untagged_archive.py"
    spec = importlib.util.spec_from_file_location("review_untagged_archive", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_is_review_candidate():
    mod = _load()
    assert mod.is_review_candidate(None, include_unknown_only=False) is True
    assert mod.is_review_candidate("", include_unknown_only=False) is True
    assert mod.is_review_candidate("[]", include_unknown_only=False) is True
    assert mod.is_review_candidate('["POLITICAL"]', include_unknown_only=False) is False
    assert mod.is_review_candidate('["UNKNOWN"]', include_unknown_only=False) is False
    assert mod.is_review_candidate('["UNKNOWN"]', include_unknown_only=True) is True


def test_fetch_candidates_sql(tmp_path):
    mod = _load()
    db = tmp_path / "c.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE POLITICAL_archive (rowid INTEGER PRIMARY KEY, date INTEGER, text TEXT, handle_id INTEGER)"
    )
    conn.execute("CREATE TABLE handle (rowid INTEGER PRIMARY KEY, id TEXT)")
    conn.execute("INSERT INTO handle (rowid, id) VALUES (1, '+1')")
    conn.execute(
        f"ALTER TABLE POLITICAL_archive ADD COLUMN {mod.COL} TEXT"
    )
    conn.execute(
        "INSERT INTO POLITICAL_archive (rowid, date, text, handle_id, classifier_attributes) "
        "VALUES (?,?,?,?,?)",
        (1, 1000, "a", 1, None),
    )
    conn.execute(
        "INSERT INTO POLITICAL_archive (rowid, date, text, handle_id, classifier_attributes) "
        "VALUES (?,?,?,?,?)",
        (2, 2000, "b", 1, json.dumps(["POLITICAL"])),
    )
    conn.commit()

    rows = mod.fetch_candidates(conn, include_unknown_only=False, limit=10)
    assert len(rows) == 1
    assert rows[0]["rowid"] == 1
    conn.close()


def test_fetch_includes_unknown_only_when_requested(tmp_path):
    mod = _load()
    db = tmp_path / "c.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE POLITICAL_archive (rowid INTEGER PRIMARY KEY, date INTEGER, text TEXT, classifier_attributes TEXT)"
    )
    conn.execute(
        "INSERT INTO POLITICAL_archive (rowid, date, text, classifier_attributes) VALUES (?,?,?,?)",
        (1, 1, "x", json.dumps(["UNKNOWN"])),
    )
    conn.execute(
        "INSERT INTO POLITICAL_archive (rowid, date, text, classifier_attributes) VALUES (?,?,?,?)",
        (2, 2, "y", json.dumps(["POLITICAL"])),
    )
    conn.commit()
    rows = mod.fetch_candidates(conn, include_unknown_only=True, limit=10)
    ids = {r["rowid"] for r in rows}
    assert 1 in ids
    assert 2 not in ids
    conn.close()
