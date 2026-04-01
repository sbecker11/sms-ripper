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
        classifier_attributes_raw=b'["spam"]',
        generated_at_iso="2026-01-01T00:00:00Z",
    )
    assert state["text"] == "hello \ufffd voter id"
    assert state["subject"] == "subj"
    assert state["handle"] == "+1"
    assert state["date_ns"] == 1000000000000000000
    assert state["classifier_attributes"] == ["spam"]
    spam = next(t for t in state["tags"] if t["tag"] == "spam")
    assert spam["model_include_guards"] == ""
    assert spam["model_exclude_guards"] == ""
    conn.close()


def _minimal_archive_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE handle (rowid INTEGER PRIMARY KEY, id TEXT)")
    conn.execute("INSERT INTO handle (rowid, id) VALUES (1, '+15550001111')")
    conn.execute(
        "CREATE TABLE message_tags_archive ("
        "rowid INTEGER PRIMARY KEY, date INTEGER, text TEXT, handle_id INTEGER, "
        "classifier_attributes TEXT)"
    )
    conn.execute(
        "INSERT INTO message_tags_archive (rowid, date, text, handle_id, classifier_attributes) "
        "VALUES (1, 1000000000000000000, 'hello voter id test', 1, '[\"spam\"]')"
    )
    conn.commit()
    return conn


def _human_payload() -> list[dict[str, object]]:
    return [
        {"tag": t, "human_checked": t == "legit", "human_keywords": "note" if t == "legit" else ""}
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
    assert att.TABLE_TAG_GUARDS in names
    assert att.TABLE_TAG_GUARD_EVENTS in names
    assert att.TABLE_TAG_GUARD_SNAPSHOTS in names
    assert att.TABLE_AUTHOR_TRUST in names
    conn.close()


def test_build_message_state_political_fallback_when_tagged_but_no_heuristic(tmp_path: Path):
    db = tmp_path / "c.db"
    conn = _minimal_archive_db(db)
    conn.execute(
        "UPDATE message_tags_archive SET text = 'plain text with no political markers', "
        "classifier_attributes = '[\"education\"]' WHERE rowid = 1"
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
        classifier_attributes_raw='["education"]',
        generated_at_iso="2026-01-01T00:00:00Z",
    )
    polit = next(t for t in state["tags"] if t["tag"] == "education")
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
        classifier_attributes_raw='["education","spam"]',
        generated_at_iso="2026-01-01T00:00:00Z",
    )
    unk = next(t for t in state["tags"] if t["tag"] == "unknown")
    assert unk["llm_checked"] is True
    assert unk["human_checked"] is False
    assert unk["llm_weight"] == 1.0
    polit = next(t for t in state["tags"] if t["tag"] == "education")
    assert polit["llm_checked"] is False
    assert polit["human_checked"] is False
    assert polit["llm_weight"] is None
    spam = next(t for t in state["tags"] if t["tag"] == "spam")
    assert spam["llm_checked"] is False
    assert spam["human_checked"] is False
    assert spam["llm_weight"] is None
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
        classifier_attributes_raw='["spam"]',
        generated_at_iso="2026-01-01T00:00:00Z",
    )
    assert state["rowid"] == 1
    assert len(state["tags"]) == len(att.TRAINING_TAGS)
    spam = next(t for t in state["tags"] if t["tag"] == "spam")
    assert spam["llm_checked"] is True
    assert spam["human_checked"] is True
    polit = next(t for t in state["tags"] if t["tag"] == "education")
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
        assert "legit" in human_guidance
        return att.classifier.ClassificationResult(
            ["legit", "personal"],
            "mock",
            {"legit": 1.0, "personal": 1.0},
        )

    with patch.object(att.classifier, "classify_message", side_effect=fake_classify):
        attrs, reason = att.apply_regenerate(
            conn,
            archive_rowid=1,
            message_text="hello voter id test",
            message_subject=None,
            human_tag_rows=_human_payload(),
        )
    assert attrs == ["legit", "personal"]
    assert reason == "mock"
    raw = conn.execute(
        "SELECT classifier_attributes FROM message_tags_archive WHERE rowid = 1"
    ).fetchone()[0]
    assert json.loads(raw) == json.loads(
        att.classifier.encode_classifier_blob(
            ["legit", "personal"], {"legit": 1.0, "personal": 1.0}
        )
    )
    row = conn.execute(
        f"SELECT llm_checked, human_checked FROM {att.TABLE_TRAINING} "
        "WHERE archive_rowid = 1 AND tag = 'legit'"
    ).fetchone()
    assert row == (1, 1)
    kw = conn.execute(
        f"SELECT llm_keywords FROM {att.TABLE_TRAINING} "
        "WHERE archive_rowid = 1 AND tag = 'legit'"
    ).fetchone()[0]
    assert kw == "note"
    ts = conn.execute(
        f"SELECT last_training_regenerate_at FROM {att.TABLE_META} WHERE archive_rowid = 1"
    ).fetchone()[0]
    assert ts and str(ts).startswith("20")
    conn.close()


def test_apply_regenerate_unchecked_keyword_becomes_tag_veto_guard(tmp_path: Path):
    db = tmp_path / "c.db"
    conn = _minimal_archive_db(db)
    att.ensure_training_tables(conn)

    def fake_classify(text: str, *, human_guidance: str | None = None):
        return att.classifier.ClassificationResult(
            ["education"],
            "mock",
            {"education": 1.0},
        )

    rows = []
    for t in att.TRAINING_TAGS:
        if t == "education":
            rows.append({"tag": t, "human_checked": False, "human_keywords": "urgent"})
        else:
            rows.append({"tag": t, "human_checked": False, "human_keywords": ""})
    with patch.object(att.classifier, "classify_message", side_effect=fake_classify):
        attrs, _reason = att.apply_regenerate(
            conn,
            archive_rowid=1,
            message_text="urgent campaign update",
            message_subject=None,
            human_tag_rows=rows,
            author="tester-a",
        )
    assert attrs == ["unknown"]
    g = conn.execute(
        f"SELECT include_keywords, exclude_keywords FROM {att.TABLE_TAG_GUARDS} WHERE tag = 'education'"
    ).fetchone()
    assert g is not None
    assert (g[0] or "").strip() == ""
    assert "urgent" in str(g[1] or "")
    ev = conn.execute(
        f"SELECT action, token, author FROM {att.TABLE_TAG_GUARD_EVENTS} "
        "WHERE tag = 'education' ORDER BY id"
    ).fetchall()
    assert any(r[0] == "add_exclude" and "urgent" in str(r[1]) and r[2] == "tester-a" for r in ev)
    snap = conn.execute(
        f"SELECT version, author, exclude_keywords FROM {att.TABLE_TAG_GUARD_SNAPSHOTS} "
        "WHERE tag = 'education' ORDER BY version DESC LIMIT 1"
    ).fetchone()
    assert snap is not None
    assert int(snap[0]) >= 1 and str(snap[1]) == "tester-a" and "urgent" in str(snap[2] or "")
    conn.close()


def test_apply_regenerate_checked_keyword_becomes_include_guard(tmp_path: Path):
    db = tmp_path / "c.db"
    conn = _minimal_archive_db(db)
    att.ensure_training_tables(conn)

    def fake_classify(text: str, *, human_guidance: str | None = None):
        return att.classifier.ClassificationResult(
            ["legit"],
            "mock",
            {"legit": 1.0},
        )

    rows = []
    for t in att.TRAINING_TAGS:
        if t == "spam":
            rows.append({"tag": t, "human_checked": True, "human_keywords": "invoice"})
        else:
            rows.append({"tag": t, "human_checked": False, "human_keywords": ""})
    with patch.object(att.classifier, "classify_message", side_effect=fake_classify):
        attrs, _reason = att.apply_regenerate(
            conn,
            archive_rowid=1,
            message_text="invoice available now",
            message_subject=None,
            human_tag_rows=rows,
            author="reviewer-1",
        )
    assert "spam" in attrs
    g = conn.execute(
        f"SELECT include_keywords, exclude_keywords FROM {att.TABLE_TAG_GUARDS} WHERE tag = 'spam'"
    ).fetchone()
    assert g is not None
    assert "invoice" in str(g[0] or "")
    ev = conn.execute(
        f"SELECT action, token, author FROM {att.TABLE_TAG_GUARD_EVENTS} "
        "WHERE tag = 'spam' ORDER BY id"
    ).fetchall()
    assert any(r[0] == "add_include" and "invoice" in str(r[1]) and r[2] == "reviewer-1" for r in ev)
    snaps = conn.execute(
        f"SELECT version, author, include_keywords FROM {att.TABLE_TAG_GUARD_SNAPSHOTS} "
        "WHERE tag = 'spam' ORDER BY version"
    ).fetchall()
    assert snaps
    assert int(snaps[-1][0]) >= 1 and str(snaps[-1][1]) == "reviewer-1"
    # Lower trust for the same author, then re-run and confirm include boost is dampened.
    conn.execute(
        f"INSERT OR REPLACE INTO {att.TABLE_AUTHOR_TRUST} (author, trust_score, updated_at) VALUES (?, ?, ?)",
        ("reviewer-1", 0.2, att.utc_now_iso()),
    )
    conn.commit()
    with patch.object(att.classifier, "classify_message", side_effect=fake_classify):
        attrs2, _reason2 = att.apply_regenerate(
            conn,
            archive_rowid=1,
            message_text="invoice available now",
            message_subject=None,
            human_tag_rows=rows,
            author="reviewer-1",
        )
    assert "spam" in attrs2
    raw2 = conn.execute(
        "SELECT classifier_attributes FROM message_tags_archive WHERE rowid = 1"
    ).fetchone()[0]
    _attrs2, w2 = att.classifier.decode_classifier_blob(raw2)
    assert w2.get("spam", 1.0) < 0.85
    conn.close()


def test_apply_regenerate_explicit_guard_fields_override_legacy_keyword_behavior(tmp_path: Path):
    db = tmp_path / "c.db"
    conn = _minimal_archive_db(db)
    att.ensure_training_tables(conn)

    def fake_classify(text: str, *, human_guidance: str | None = None):
        return att.classifier.ClassificationResult(
            ["education"],
            "mock",
            {"education": 1.0},
        )

    rows = []
    for t in att.TRAINING_TAGS:
        if t == "education":
            rows.append(
                {
                    "tag": t,
                    "human_checked": False,
                    "human_keywords": "legacy-plain-should-not-auto-veto",
                    "human_include_guards": "",
                    "human_exclude_guards": "explicit-veto",
                }
            )
        else:
            rows.append(
                {
                    "tag": t,
                    "human_checked": False,
                    "human_keywords": "",
                    "human_include_guards": "",
                    "human_exclude_guards": "",
                }
            )

    with patch.object(att.classifier, "classify_message", side_effect=fake_classify):
        attrs, _reason = att.apply_regenerate(
            conn,
            archive_rowid=1,
            message_text="explicit-veto message",
            message_subject=None,
            human_tag_rows=rows,
            author="tester-explicit",
        )
    assert attrs == ["unknown"]
    g = conn.execute(
        f"SELECT include_keywords, exclude_keywords FROM {att.TABLE_TAG_GUARDS} WHERE tag = 'education'"
    ).fetchone()
    assert g is not None
    assert "explicit-veto" in str(g[1] or "")
    assert "legacy-plain-should-not-auto-veto" not in str(g[1] or "")
    conn.close()


def test_apply_regenerate_empty_subject_and_body_forces_unknown_without_model_call(tmp_path: Path):
    db = tmp_path / "c.db"
    conn = _minimal_archive_db(db)
    att.ensure_training_tables(conn)

    rows = []
    for t in att.TRAINING_TAGS:
        rows.append({"tag": t, "human_checked": t == "spam", "human_keywords": "anything"})

    with patch.object(att.classifier, "classify_message") as cls_mock:
        attrs, reason = att.apply_regenerate(
            conn,
            archive_rowid=1,
            message_text="",
            message_subject="",
            human_tag_rows=rows,
            author="tester-empty",
        )
    cls_mock.assert_not_called()
    assert attrs == ["unknown"]
    assert "forced unknown" in reason
    raw = conn.execute(
        "SELECT classifier_attributes FROM message_tags_archive WHERE rowid = 1"
    ).fetchone()[0]
    _attrs, w = att.classifier.decode_classifier_blob(raw)
    assert _attrs == ["unknown"]
    assert w.get("unknown") == 1.0
    conn.close()
