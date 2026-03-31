"""Tests for scripts/reclassify_archive_tags.py."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

import archive
import classifier

_REPO = Path(__file__).resolve().parent.parent


def _load_module():
    path = _REPO / "scripts" / "reclassify_archive_tags.py"
    spec = importlib.util.spec_from_file_location("reclassify_archive_tags", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_reclassify_updates_classifier_attributes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    mod = _load_module()
    db = tmp_path / "chat.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE POLITICAL_archive (rowid INTEGER PRIMARY KEY, text TEXT)"
    )
    archive._ensure_classifier_attributes_column(conn, "POLITICAL_archive")
    conn.execute(
        "INSERT INTO POLITICAL_archive (rowid, text, classifier_attributes) VALUES (1, 'x', '[\"UNKNOWN\"]')"
    )
    conn.commit()

    monkeypatch.setattr(
        mod.classifier,
        "classify_message",
        lambda t: classifier.ClassificationResult(
            ["POLITICAL", "SPAM"],
            "ok",
            {"POLITICAL": 1.0, "SPAM": 1.0},
        ),
    )

    u, same, err = mod.reclassify_archive_tags(
        conn, dry_run=False, limit=None, delay_sec=0.0
    )
    assert err == 0
    assert u == 1
    assert same == 0

    raw = conn.execute(
        "SELECT classifier_attributes FROM POLITICAL_archive WHERE rowid = 1"
    ).fetchone()[0]
    assert json.loads(raw) == json.loads(
        classifier.encode_classifier_blob(
            ["POLITICAL", "SPAM"], {"POLITICAL": 1.0, "SPAM": 1.0}
        )
    )
    conn.close()


def test_reclassify_dry_run_no_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    mod = _load_module()
    db = tmp_path / "chat.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE POLITICAL_archive (rowid INTEGER PRIMARY KEY, text TEXT)")
    archive._ensure_classifier_attributes_column(conn, "POLITICAL_archive")
    conn.execute(
        'INSERT INTO POLITICAL_archive (rowid, text, classifier_attributes) VALUES (1, "y", \'["LEGIT"]\')'
    )
    conn.commit()

    monkeypatch.setattr(
        mod.classifier,
        "classify_message",
        lambda t: classifier.ClassificationResult(["POLITICAL"], "ok", {"POLITICAL": 1.0}),
    )

    u, same, err = mod.reclassify_archive_tags(
        conn, dry_run=True, limit=None, delay_sec=0.0
    )
    assert err == 0
    assert u == 1
    raw = conn.execute(
        "SELECT classifier_attributes FROM POLITICAL_archive WHERE rowid = 1"
    ).fetchone()[0]
    assert json.loads(raw) == ["LEGIT"]
    conn.close()
