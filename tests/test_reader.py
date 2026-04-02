"""Tests for reader.apple_ts_to_datetime, datetime_to_apple_ts, get_recent_messages."""

from datetime import datetime, timedelta, timezone

import pytest

import config
import reader
from tests.conftest import populate_chat_db


def test_apple_ts_to_datetime_none_and_zero():
    assert reader.apple_ts_to_datetime(None) is None
    assert reader.apple_ts_to_datetime(0) is None


def test_apple_ts_roundtrip_utc():
    original = datetime(2024, 6, 15, 10, 30, 0, tzinfo=timezone.utc)
    ns = reader.datetime_to_apple_ts(original)
    back = reader.apple_ts_to_datetime(ns)
    assert back == original.replace(tzinfo=None)


def test_get_recent_messages_inbound_only(chat_db_path, monkeypatch):
    monkeypatch.setattr(config, "LOOKBACK_MINUTES", 120)
    monkeypatch.setattr(config, "MESSAGE_FETCH_LIMIT", 10)

    now_ns = reader.datetime_to_apple_ts(datetime.utcnow())
    populate_chat_db(
        chat_db_path,
        text="from them",
        is_from_me=0,
        date_ns=now_ns,
        sender="+19998887777",
    )
    populate_chat_db(
        chat_db_path,
        text="from me",
        is_from_me=1,
        date_ns=now_ns + 1,
        sender="+19998887777",
    )

    inbound = reader.get_recent_messages(limit=10, lookback_minutes=120, inbound_only=True)
    assert len(inbound) == 1
    assert inbound[0].text == "from them"
    assert inbound[0].is_from_me is False

    all_msgs = reader.get_recent_messages(limit=10, lookback_minutes=120, inbound_only=False)
    assert len(all_msgs) == 2


def test_get_recent_messages_filters_tapback_and_empty(chat_db_path, monkeypatch):
    monkeypatch.setattr(config, "LOOKBACK_MINUTES", 60)
    monkeypatch.setattr(config, "MESSAGE_FETCH_LIMIT", 20)

    now_ns = reader.datetime_to_apple_ts(datetime.utcnow())
    populate_chat_db(
        chat_db_path,
        text="reaction",
        date_ns=now_ns,
        associated_message_type=2000,
    )
    populate_chat_db(
        chat_db_path,
        text="",
        date_ns=now_ns + 2,
        associated_message_type=0,
    )
    populate_chat_db(
        chat_db_path,
        text="real",
        date_ns=now_ns + 3,
        associated_message_type=0,
    )

    msgs = reader.get_recent_messages(limit=20, lookback_minutes=60)
    assert len(msgs) == 1
    assert msgs[0].text == "real"


def test_get_recent_messages_includes_attributed_body_when_text_empty(
    chat_db_path, monkeypatch
):
    monkeypatch.setattr(config, "LOOKBACK_MINUTES", 60)
    monkeypatch.setattr(config, "MESSAGE_FETCH_LIMIT", 20)

    now_ns = reader.datetime_to_apple_ts(datetime.utcnow())
    populate_chat_db(
        chat_db_path,
        text="",
        date_ns=now_ns,
        attributed_body=b"\x00\x01\x02",
    )

    msgs = reader.get_recent_messages(limit=20, lookback_minutes=60)
    assert len(msgs) == 1
    assert msgs[0].text == reader.RICH_ONLY_PLACEHOLDER


def test_get_recent_messages_respects_lookback(chat_db_path, monkeypatch):
    monkeypatch.setattr(config, "LOOKBACK_MINUTES", 5)
    monkeypatch.setattr(config, "MESSAGE_FETCH_LIMIT", 50)

    old_ns = reader.datetime_to_apple_ts(datetime.utcnow() - timedelta(minutes=999))
    populate_chat_db(chat_db_path, text="stale", date_ns=old_ns)

    assert reader.get_recent_messages(limit=50, lookback_minutes=5) == []


def test_message_display_with_and_without_date():
    with_date = reader.Message(
        rowid=1,
        chat_id=1,
        chat_identifier="+1",
        sender="+1",
        text="short",
        is_from_me=False,
        date=datetime(2024, 3, 1, 15, 0, 0),
    )
    assert "2024-03-01" in with_date.display()
    assert "←" in with_date.display()

    out = reader.Message(
        rowid=1,
        chat_id=1,
        chat_identifier="+1",
        sender=None,
        text="x",
        is_from_me=True,
        date=None,
    )
    d = out.display()
    assert "unknown" in d
    assert "→ ME" in d


def test_plain_text_is_user_stop_command():
    assert reader.plain_text_is_user_stop_command("STOP") is True
    assert reader.plain_text_is_user_stop_command("  stop.  ") is True
    assert reader.plain_text_is_user_stop_command("STOP please") is False
    assert reader.plain_text_is_user_stop_command("") is False


def test_get_recent_outbound_stop_replies_finds_stop_only(chat_db_path, monkeypatch):
    monkeypatch.setattr(config, "LOOKBACK_MINUTES", 120)
    now_ns = reader.datetime_to_apple_ts(datetime.utcnow())
    populate_chat_db(
        chat_db_path,
        text="STOP",
        is_from_me=1,
        date_ns=now_ns,
        sender="+19998887777",
    )
    populate_chat_db(
        chat_db_path,
        text="not stop",
        is_from_me=1,
        date_ns=now_ns + 1,
        sender="+19998887777",
    )
    out = reader.get_recent_outbound_stop_replies(limit=10, lookback_minutes=120)
    assert len(out) == 1
    assert out[0].text == "STOP"
    assert out[0].is_from_me is True


def test_get_recent_messages_missing_db_raises(tmp_path, monkeypatch):
    missing = tmp_path / "nope.db"
    monkeypatch.setattr(config, "CHAT_DB_PATH", str(missing))
    with pytest.raises(FileNotFoundError, match="chat.db not found"):
        reader.get_recent_messages(limit=5, lookback_minutes=60)
