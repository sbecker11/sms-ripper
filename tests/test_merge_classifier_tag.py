"""End-to-end merge of tag A into tag B (catalog, archive JSON, training, guards)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import archive
import archive_tag_training as att
import classifier
import tag_catalog


def test_merge_tag_in_classifier_blob_list() -> None:
    raw = '["spam", "education"]'
    new_blob, changed = classifier.merge_tag_in_classifier_blob(raw, "spam", "promo")
    assert changed and new_blob is not None
    d = json.loads(new_blob)
    assert d["attributes"] == ["promo", "education"]


def test_merge_tag_in_classifier_blob_dedupe_weights() -> None:
    raw = classifier.encode_classifier_blob(
        ["spam", "promo"], {"spam": 0.91, "promo": 0.5}
    )
    new_blob, changed = classifier.merge_tag_in_classifier_blob(raw, "spam", "promo")
    assert changed and new_blob is not None
    d = json.loads(new_blob)
    assert d["attributes"] == ["promo"]
    assert d["weights"]["promo"] == 0.91


def test_merge_tag_in_classifier_blob_noop() -> None:
    new_blob, changed = classifier.merge_tag_in_classifier_blob(
        '["education"]', "spam", "promo"
    )
    assert not changed
    assert new_blob is None


def test_merge_classifier_tag_into_rewrites_archive_and_removes_source(tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    conn = sqlite3.connect(db)
    try:
        att.ensure_training_tables(conn)
        conn.execute(
            "CREATE TABLE message_tags_archive ("
            "rowid INTEGER PRIMARY KEY, text TEXT, classifier_attributes TEXT)"
        )
        archive._ensure_classifier_attributes_column(conn, "message_tags_archive")
        blob1 = classifier.encode_classifier_blob(
            ["social", "promo"], {"social": 0.8, "promo": 0.3}
        )
        conn.execute(
            "INSERT INTO message_tags_archive (rowid, text, classifier_attributes) "
            "VALUES (1, 'a', ?), (2, 'b', ?)",
            (blob1, '["social"]'),
        )
        conn.execute(
            f"""
            INSERT INTO {att.TABLE_TRAINING}
            (archive_rowid, tag, llm_checked, llm_keywords, human_checked, human_keywords)
            VALUES (1, 'social', 1, '', NULL, ''),
                   (1, 'promo', 0, '', NULL, ''),
                   (2, 'social', 1, '', NULL, '')
            """
        )
        conn.execute(
            f"""
            INSERT INTO {att.TABLE_TAG_GUARDS} (tag, include_keywords, exclude_keywords, updated_at)
            VALUES ('social', 'x', '', ?), ('promo', 'y', '', ?)
            """,
            (att.utc_now_iso(), att.utc_now_iso()),
        )
        conn.commit()

        n = att.merge_classifier_tag_into(conn, "social", "promo")
        conn.commit()

        assert n == 2
        assert not conn.execute(
            f"SELECT 1 FROM {tag_catalog.TABLE_TAG_CATALOG} WHERE tag = 'social'"
        ).fetchone()
        r1 = conn.execute(
            "SELECT classifier_attributes FROM message_tags_archive WHERE rowid = 1"
        ).fetchone()[0]
        d1 = json.loads(r1)
        assert d1["attributes"] == ["promo"]
        assert d1["weights"]["promo"] == 0.8
        r2 = conn.execute(
            "SELECT classifier_attributes FROM message_tags_archive WHERE rowid = 2"
        ).fetchone()[0]
        assert json.loads(r2)["attributes"] == ["promo"]
        rows = conn.execute(
            f"SELECT tag FROM {att.TABLE_TRAINING} WHERE archive_rowid = 1 ORDER BY tag"
        ).fetchall()
        assert [r[0] for r in rows] == ["promo"]
        g = conn.execute(
            f"SELECT include_keywords FROM {att.TABLE_TAG_GUARDS} WHERE tag = 'promo'"
        ).fetchone()[0]
        assert "x" in (g or "") and "y" in (g or "")
        assert not conn.execute(
            f"SELECT 1 FROM {att.TABLE_TAG_GUARDS} WHERE tag = 'social'"
        ).fetchone()
    finally:
        conn.close()


def test_merge_classifier_tag_into_requires_target(tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    conn = sqlite3.connect(db)
    try:
        att.ensure_training_tables(conn)
        with pytest.raises(ValueError, match="target"):
            att.merge_classifier_tag_into(conn, "social", "nonexistent_tag_xyz")
    finally:
        conn.close()


def test_delete_catalog_tag_rejects_unknown(tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    conn = sqlite3.connect(db)
    try:
        tag_catalog.ensure_tag_catalog(conn)
        with pytest.raises(ValueError, match="unknown"):
            tag_catalog.delete_catalog_tag(conn, "unknown")
    finally:
        conn.close()
