"""Tests for dynamic tag catalog (sms_ripper_tag_catalog)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import archive_tag_training as att
import tag_catalog


def test_validate_new_tag_key() -> None:
    assert tag_catalog.validate_new_tag_key("My_Tag") == "my_tag"
    with pytest.raises(ValueError):
        tag_catalog.validate_new_tag_key("")
    with pytest.raises(ValueError):
        tag_catalog.validate_new_tag_key("bad-tag")


def test_list_catalog_rows_seeded(tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    conn = sqlite3.connect(db)
    try:
        rows = tag_catalog.list_catalog_rows(conn)
        keys = {r["tag_key"] for r in rows}
        assert keys == {
            "church",
            "education",
            "personal",
            "promo",
            "social",
            "sofi",
            "spam",
            "stop",
            "transactional",
            "unknown",
        }
        assert any(r["tag"] == "education" and r["active"] for r in rows)
    finally:
        conn.close()


def test_cannot_deactivate_last_active_tag(tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    conn = sqlite3.connect(db)
    try:
        tag_catalog.ensure_tag_catalog(conn)
        conn.execute(
            f"UPDATE {tag_catalog.TABLE_TAG_CATALOG} SET active = 0"
        )
        conn.execute(
            f"UPDATE {tag_catalog.TABLE_TAG_CATALOG} SET active = 1 WHERE tag = 'unknown'"
        )
        conn.commit()
        with pytest.raises(ValueError, match="last active"):
            tag_catalog.set_tag_flags(conn, "unknown", active=False)
    finally:
        conn.close()


def test_upsert_add_tag(tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    conn = sqlite3.connect(db)
    try:
        tag_catalog.ensure_tag_catalog(conn)
        tag_catalog.upsert_tag_row(conn, "finance", active=True, archive_enabled=False)
        conn.commit()
        rows = tag_catalog.list_catalog_rows(conn)
        assert any(r["tag_key"] == "finance" for r in rows)
    finally:
        conn.close()


def test_merge_spam_into_junk_mail_updates_training_and_catalog(tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    conn = sqlite3.connect(db)
    try:
        att.ensure_training_tables(conn)
        tag_catalog.upsert_tag_row(conn, "junk_mail", active=True, archive_enabled=False)
        conn.execute(
            f"""
            INSERT INTO {att.TABLE_TRAINING}
            (archive_rowid, tag, llm_checked, llm_keywords, human_checked, human_keywords)
            VALUES (1, 'spam', 1, '', NULL, '')
            """
        )
        conn.commit()
        att.merge_classifier_tag_into(conn, "spam", "junk_mail")
        conn.commit()
        row = conn.execute(
            f"SELECT tag FROM {att.TABLE_TRAINING} WHERE archive_rowid = 1"
        ).fetchone()
        assert row is not None
        assert row[0] == "junk_mail"
        cat = conn.execute(
            f"SELECT tag FROM {tag_catalog.TABLE_TAG_CATALOG} WHERE tag = 'junk_mail'"
        ).fetchone()
        assert cat is not None
        assert not conn.execute(
            f"SELECT 1 FROM {tag_catalog.TABLE_TAG_CATALOG} WHERE tag = 'spam'"
        ).fetchone()
    finally:
        conn.close()
