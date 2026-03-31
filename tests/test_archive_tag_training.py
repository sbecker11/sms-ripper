"""Tests for archive_tag_training DB helpers and regenerate flow."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import archive_tag_training as att


def test_coerce_apple_timestamp_ns_float_string():
    assert att.coerce_apple_timestamp_ns("1.5e18") == 1500000000000000000


def test_merge_llm_and_human_keyword_hints():
    assert att.merge_llm_and_human_keyword_hints("usa-26.io", "my note") == "usa-26.io, my note"
    assert att.merge_llm_and_human_keyword_hints("a, b", "b, c") == "a, b, c"
    assert att.merge_llm_and_human_keyword_hints("", "only human") == "only human"


def test_strip_human_tokens_already_in_merged_llm():
    assert att.strip_human_tokens_already_in_merged_llm("usa-26.io, extra", "usa-26.io, extra") == ""
    assert att.strip_human_tokens_already_in_merged_llm("profile", "profile") == ""
    assert att.strip_human_tokens_already_in_merged_llm("profile", "profile, other") == ""
    assert att.strip_human_tokens_already_in_merged_llm("only", "profile") == "only"


def test_build_message_state_bytes_fields(tmp_path: Path):
    db = tmp_path / "c.db"
    conn = sqlite3.connect(db)
    att.ensure_training_tables(conn)
    state = att.build_message_state(
        conn,
        archive_rowid=1,
        text=b"hello \xff voter id",
        subject=b"subj",
        handle=b"+1",
        date_ns=b"1000000000000000000",
        classifier_attributes_raw=b'["SPAM"]',
        generated_at_iso="2026-01-01T00:00:00Z",
    )
    assert state["text"] == "hello \ufffd voter id"
    assert state["subject"] == "subj"
    assert state["handle"] == "+1"
    assert state["date_ns"] == 1000000000000000000
    assert state["classifier_attributes"] == ["SPAM"]
    conn.close()


def _minimal_archive_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE handle (rowid INTEGER PRIMARY KEY, id TEXT)")
    conn.execute("INSERT INTO handle (rowid, id) VALUES (1, '+15550001111')")
    conn.execute(
        "CREATE TABLE POLITICAL_archive ("
        "rowid INTEGER PRIMARY KEY, date INTEGER, text TEXT, handle_id INTEGER, "
        "classifier_attributes TEXT)"
    )
    conn.execute(
        "INSERT INTO POLITICAL_archive (rowid, date, text, handle_id, classifier_attributes) "
        "VALUES (1, 1000000000000000000, 'hello voter id test', 1, '[\"SPAM\"]')"
    )
    conn.commit()
    return conn


def _human_payload() -> list[dict[str, object]]:
    return [
        {"tag": t, "human_checked": t == "LEGIT", "human_keywords": "note" if t == "LEGIT" else ""}
        for t in att.TRAINING_TAGS
    ]


def test_ensure_training_tables_idempotent(tmp_path: Path):
    db = tmp_path / "c.db"
    conn = sqlite3.connect(db)
    att.ensure_training_tables(conn)
    att.ensure_training_tables(conn)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'sms_ripper_%'"
    ).fetchall()
    names = {r[0] for r in rows}
    assert att.TABLE_TRAINING in names
    assert att.TABLE_META in names
    conn.close()


def test_build_message_state_political_fallback_when_tagged_but_no_heuristic(tmp_path: Path):
    db = tmp_path / "c.db"
    conn = _minimal_archive_db(db)
    conn.execute(
        "UPDATE POLITICAL_archive SET text = 'plain text with no political markers', "
        "classifier_attributes = '[\"POLITICAL\"]' WHERE rowid = 1"
    )
    conn.commit()
    att.ensure_training_tables(conn)
    state = att.build_message_state(
        conn,
        archive_rowid=1,
        text="plain text with no political markers",
        subject=None,
        handle="+15550001111",
        date_ns=10**18,
        classifier_attributes_raw='["POLITICAL"]',
        generated_at_iso="2026-01-01T00:00:00Z",
    )
    polit = next(t for t in state["tags"] if t["tag"] == "POLITICAL")
    assert polit["llm_checked"] is True
    assert "[no heuristic keyword match" in (polit.get("llm_keywords") or "")
    conn.close()


def test_build_message_state_empty_body_unknown_llm_checked_without_json_tag(tmp_path: Path):
    db = tmp_path / "c.db"
    conn = _minimal_archive_db(db)
    att.ensure_training_tables(conn)
    state = att.build_message_state(
        conn,
        archive_rowid=1,
        text="",
        subject="",
        handle="+1",
        date_ns=10**18,
        classifier_attributes_raw='["POLITICAL","SPAM"]',
        generated_at_iso="2026-01-01T00:00:00Z",
    )
    unk = next(t for t in state["tags"] if t["tag"] == "UNKNOWN")
    assert unk["llm_checked"] is True
    assert unk["human_checked"] is False
    polit = next(t for t in state["tags"] if t["tag"] == "POLITICAL")
    assert polit["llm_checked"] is False
    assert polit["human_checked"] is False
    spam = next(t for t in state["tags"] if t["tag"] == "SPAM")
    assert spam["llm_checked"] is False
    assert spam["human_checked"] is False
    conn.close()


def test_build_message_state_no_training_rows(tmp_path: Path):
    db = tmp_path / "c.db"
    conn = _minimal_archive_db(db)
    att.ensure_training_tables(conn)
    state = att.build_message_state(
        conn,
        archive_rowid=1,
        text="hello voter id test",
        subject=None,
        handle="+15550001111",
        date_ns=10**18,
        classifier_attributes_raw='["SPAM"]',
        generated_at_iso="2026-01-01T00:00:00Z",
    )
    assert state["rowid"] == 1
    assert len(state["tags"]) == len(att.TRAINING_TAGS)
    spam = next(t for t in state["tags"] if t["tag"] == "SPAM")
    assert spam["llm_checked"] is True
    assert spam["human_checked"] is True
    polit = next(t for t in state["tags"] if t["tag"] == "POLITICAL")
    assert polit["llm_checked"] is False
    assert "voter id" in (polit["llm_keywords"] or "").lower()
    conn.close()


def test_apply_regenerate_updates_archive_and_training(tmp_path: Path):
    db = tmp_path / "c.db"
    conn = _minimal_archive_db(db)
    att.ensure_training_tables(conn)

    def fake_classify(text: str, *, human_guidance: str | None = None):
        assert text == "hello voter id test"
        assert human_guidance is not None
        assert "LEGIT" in human_guidance
        return att.classifier.ClassificationResult(
            ["LEGIT", "PERSONAL"],
            "mock",
            {"LEGIT": 1.0, "PERSONAL": 1.0},
        )

    with patch.object(att.classifier, "classify_message", side_effect=fake_classify):
        attrs, reason = att.apply_regenerate(
            conn,
            archive_rowid=1,
            message_text="hello voter id test",
            human_tag_rows=_human_payload(),
        )
    assert attrs == ["LEGIT", "PERSONAL"]
    assert reason == "mock"
    raw = conn.execute(
        "SELECT classifier_attributes FROM POLITICAL_archive WHERE rowid = 1"
    ).fetchone()[0]
    assert json.loads(raw) == json.loads(
        att.classifier.encode_classifier_blob(
            ["LEGIT", "PERSONAL"], {"LEGIT": 1.0, "PERSONAL": 1.0}
        )
    )
    row = conn.execute(
        f"SELECT llm_checked, human_checked FROM {att.TABLE_TRAINING} "
        "WHERE archive_rowid = 1 AND tag = 'LEGIT'"
    ).fetchone()
    assert row == (1, 1)
    kw = conn.execute(
        f"SELECT llm_keywords FROM {att.TABLE_TRAINING} "
        "WHERE archive_rowid = 1 AND tag = 'LEGIT'"
    ).fetchone()[0]
    assert kw == "note"
    ts = conn.execute(
        f"SELECT last_training_regenerate_at FROM {att.TABLE_META} WHERE archive_rowid = 1"
    ).fetchone()[0]
    assert ts and str(ts).startswith("20")
    conn.close()
