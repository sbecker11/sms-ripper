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
        "CREATE TABLE message_tags_archive (rowid INTEGER PRIMARY KEY, text TEXT)"
    )
    archive._ensure_classifier_attributes_column(conn, "message_tags_archive")
    conn.execute(
        "INSERT INTO message_tags_archive (rowid, text, classifier_attributes) VALUES (1, 'x', '[\"unknown\"]')"
    )
    conn.commit()

    monkeypatch.setattr(
        mod.classifier,
        "classify_message",
        lambda t: classifier.ClassificationResult(
            ["education", "spam"],
            "ok",
            {"education": 1.0, "spam": 1.0},
        ),
    )

    u, same, err = mod.reclassify_archive_tags(
        conn, dry_run=False, limit=None, delay_sec=0.0
    )
    assert err == 0
    assert u == 1
    assert same == 0

    raw = conn.execute(
        "SELECT classifier_attributes FROM message_tags_archive WHERE rowid = 1"
    ).fetchone()[0]
    assert json.loads(raw) == json.loads(
        classifier.encode_classifier_blob(
            ["education", "spam"], {"education": 1.0, "spam": 1.0}
        )
    )
    conn.close()


def test_reclassify_dry_run_no_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    mod = _load_module()
    db = tmp_path / "chat.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE message_tags_archive (rowid INTEGER PRIMARY KEY, text TEXT)")
    archive._ensure_classifier_attributes_column(conn, "message_tags_archive")
    conn.execute(
        'INSERT INTO message_tags_archive (rowid, text, classifier_attributes) VALUES (1, "y", \'["legit"]\')'
    )
    conn.commit()

    monkeypatch.setattr(
        mod.classifier,
        "classify_message",
        lambda t: classifier.ClassificationResult(["education"], "ok", {"education": 1.0}),
    )

    u, same, err = mod.reclassify_archive_tags(
        conn, dry_run=True, limit=None, delay_sec=0.0
    )
    assert err == 0
    assert u == 1
    raw = conn.execute(
        "SELECT classifier_attributes FROM message_tags_archive WHERE rowid = 1"
    ).fetchone()[0]
    assert json.loads(raw) == ["legit"]
    conn.close()


def test_reclassify_parallel_workers_updates_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    mod = _load_module()
    db = tmp_path / "chat.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE message_tags_archive (rowid INTEGER PRIMARY KEY, text TEXT)")
    archive._ensure_classifier_attributes_column(conn, "message_tags_archive")
    conn.execute(
        'INSERT INTO message_tags_archive (rowid, text, classifier_attributes) VALUES (1, "a", \'["unknown"]\')'
    )
    conn.execute(
        'INSERT INTO message_tags_archive (rowid, text, classifier_attributes) VALUES (2, "b", \'["unknown"]\')'
    )
    conn.commit()

    monkeypatch.setattr(
        mod.classifier,
        "classify_message",
        lambda t: classifier.ClassificationResult(["spam"], "ok", {"spam": 1.0}),
    )

    u, same, err = mod.reclassify_archive_tags(
        conn, dry_run=False, limit=None, delay_sec=0.0, workers=4, show_progress=False
    )
    assert err == 0
    assert u == 2
    assert same == 0
    rows = conn.execute(
        "SELECT classifier_attributes FROM message_tags_archive ORDER BY rowid"
    ).fetchall()
    assert all("spam" in json.loads(r[0]).get("attributes", []) for r in rows)
    conn.close()


def test_reclassify_custom_table_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    mod = _load_module()
    db = tmp_path / "chat.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE SPAM_archive (rowid INTEGER PRIMARY KEY, text TEXT)")
    archive._ensure_classifier_attributes_column(conn, "SPAM_archive")
    conn.execute(
        'INSERT INTO SPAM_archive (rowid, text, classifier_attributes) VALUES (1, "z", \'["unknown"]\')'
    )
    conn.commit()

    monkeypatch.setattr(
        mod.classifier,
        "classify_message",
        lambda t: classifier.ClassificationResult(["scam"], "ok", {"scam": 1.0}),
    )
    u, same, err = mod.reclassify_archive_tags(
        conn,
        table="SPAM_archive",
        dry_run=False,
        limit=None,
        delay_sec=0.0,
        workers=1,
        show_progress=False,
    )
    assert err == 0
    assert u == 1
    assert same == 0
    raw = conn.execute(
        "SELECT classifier_attributes FROM SPAM_archive WHERE rowid = 1"
    ).fetchone()[0]
    assert "scam" in json.loads(raw).get("attributes", [])
    conn.close()
