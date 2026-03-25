"""Tests for classifier.classify_message with mocked HTTP."""

import json
from io import BytesIO
from unittest.mock import patch

import pytest
import urllib.error

import classifier
import config


def _anthropic_response_payload(inner_json: str) -> dict:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": inner_json}],
        "model": "claude-sonnet-4-20250514",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_classify_message_skips_non_text_content_blocks(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["LEGIT"], "reason": "ok"})
    outer = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "..."},
            {"type": "text", "text": inner},
        ],
        "model": "claude-sonnet-4-20250514",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    raw = json.dumps(outer).encode()
    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        attrs, reason = classifier.classify_message("hi")
    assert attrs == ["LEGIT"]
    assert reason == "ok"


def test_classify_message_parses_json(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["spam", "stop"], "reason": "bulk sms"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()

    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        attrs, reason = classifier.classify_message("WINNER click now")

    assert attrs == ["SPAM", "STOP"]
    assert reason == "bulk sms"


def test_classify_message_invalid_payload_shape_returns_unknown(monkeypatch):
    """Valid JSON but wrong types for ClassificationPayload → ValidationError path."""
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": "not-a-list", "reason": "x"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()

    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        attrs, reason = classifier.classify_message("hello")

    assert attrs == ["UNKNOWN"]
    assert "Could not parse" in reason


def test_classify_message_malformed_inner_json_returns_unknown(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = "not json {"
    raw = json.dumps(_anthropic_response_payload(inner)).encode()

    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        attrs, reason = classifier.classify_message("hello")

    assert attrs == ["UNKNOWN"]
    assert "Could not parse" in reason


def test_classify_message_missing_api_key():
    with patch.object(config, "ANTHROPIC_API_KEY", ""):
        with pytest.raises(ValueError, match=r"ANTHROPIC_API_KEY.*\.env"):
            classifier.classify_message("hi")


def test_classify_message_adds_political_for_white_house(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["LEGIT"], "reason": "ok"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()

    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        attrs, _ = classifier.classify_message("News from the White House")

    assert "POLITICAL" in attrs
    assert "LEGIT" in attrs


def test_classify_message_adds_political_for_us_red(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["PROMO"], "reason": "offer"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()

    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        attrs, _ = classifier.classify_message("Text us-red for updates")

    assert "POLITICAL" in attrs


def test_classify_message_adds_political_for_vote_red_domain(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["SPAM"], "reason": "bulk"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()

    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        attrs, _ = classifier.classify_message("Vote now https://vote-red.io/tg9ri9")

    assert "POLITICAL" in attrs
    assert "SPAM" in attrs


def test_classify_message_http_error(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "k")

    err = urllib.error.HTTPError(
        "https://api.anthropic.com/v1/messages",
        401,
        "Unauthorized",
        {},
        BytesIO(b'{"type":"error"}'),
    )

    with patch.object(classifier.urllib.request, "urlopen", side_effect=err):
        with pytest.raises(RuntimeError, match="Claude API error 401"):
            classifier.classify_message("x")
