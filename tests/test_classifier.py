"""Tests for classifier.classify_message with mocked HTTP."""

import json
from io import BytesIO
from unittest.mock import patch

import pytest
import urllib.error

import classifier
import config
import reader


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
        res = classifier.classify_message("hi")
    assert res.attributes == ["LEGIT"]
    assert res.reason == "ok"
    assert res.weights.get("LEGIT") == 1.0


def test_classify_message_includes_human_guidance_in_request(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["LEGIT"], "reason": "ok"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()
    captured: list[dict] = []

    def capture_urlopen(req, timeout=None):
        captured.append(json.loads(req.data.decode()))
        return _FakeResponse(raw)

    with patch.object(classifier.urllib.request, "urlopen", side_effect=capture_urlopen):
        classifier.classify_message(
            "plain body",
            human_guidance="Human says treat as LEGIT.",
        )
    assert captured
    user = captured[0]["messages"][0]["content"]
    assert "plain body" in user
    assert "Human reviewer guidance" in user
    assert "Human says treat as LEGIT." in user


def test_classify_message_parses_json(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["spam", "stop"], "reason": "bulk sms"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()

    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("WINNER click now")

    assert res.attributes == ["SPAM", "STOP"]
    assert res.reason == "bulk sms"


def test_classify_message_invalid_payload_shape_returns_unknown(monkeypatch):
    """Valid JSON but wrong types for ClassificationPayload → ValidationError path."""
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": "not-a-list", "reason": "x"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()

    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("hello")

    assert res.attributes == ["UNKNOWN"]
    assert "Could not parse" in res.reason


def test_classify_message_malformed_inner_json_returns_unknown(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = "not json {"
    raw = json.dumps(_anthropic_response_payload(inner)).encode()

    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("hello")

    assert res.attributes == ["UNKNOWN"]
    assert "Could not parse" in res.reason


def test_classify_message_missing_api_key():
    with patch.object(config, "ANTHROPIC_API_KEY", ""):
        with pytest.raises(ValueError, match=r"ANTHROPIC_API_KEY.*\.env"):
            classifier.classify_message("hi")


def test_classify_message_rich_placeholder_skips_http():
    with patch.object(classifier.urllib.request, "urlopen") as urlopen_mock:
        res = classifier.classify_message(reader.RICH_ONLY_PLACEHOLDER)
    urlopen_mock.assert_not_called()
    assert res.attributes == ["POLITICAL", "SPAM"]
    assert "no API" in res.reason
    assert res.weights["POLITICAL"] == 1.0 and res.weights["SPAM"] == 1.0


def test_classify_message_empty_text_returns_unknown_skips_http():
    """No subject/body: UNKNOWN without API; works even when ANTHROPIC_API_KEY is unset."""
    with patch.object(config, "ANTHROPIC_API_KEY", ""):
        with patch.object(classifier.urllib.request, "urlopen") as urlopen_mock:
            res = classifier.classify_message("")
    urlopen_mock.assert_not_called()
    assert res.attributes == ["UNKNOWN"]
    assert "no subject or body" in res.reason
    assert res.weights == {"UNKNOWN": 1.0}

    with patch.object(classifier.urllib.request, "urlopen") as urlopen_mock:
        res2 = classifier.classify_message("   \n\t  ")
    urlopen_mock.assert_not_called()
    assert res2.attributes == ["UNKNOWN"]


def test_classify_message_empty_text_with_human_guidance_still_calls_api(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["LEGIT"], "reason": "from guidance"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()
    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)) as m:
        res = classifier.classify_message("", human_guidance="one-to-one from bank")
    m.assert_called_once()
    assert res.attributes == ["LEGIT"]


def test_classify_message_adds_political_for_white_house(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["LEGIT"], "reason": "ok"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()

    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("News from the White House")

    assert "POLITICAL" in res.attributes
    assert "LEGIT" in res.attributes
    assert res.weights["POLITICAL"] >= classifier.HEURISTIC_POLITICAL_WEIGHT


def test_classify_message_adds_political_for_us_red(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["PROMO"], "reason": "offer"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()

    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("Text us-red for updates")

    assert "POLITICAL" in res.attributes


def test_classify_message_adds_political_for_vote_red_domain(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["SPAM"], "reason": "bulk"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()

    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("Vote now https://vote-red.io/tg9ri9")

    assert "POLITICAL" in res.attributes
    assert "SPAM" in res.attributes


def test_classify_message_adds_political_for_housegop_domain(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["UNKNOWN"], "reason": "unclear"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()

    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("Complete here: https://housegop.info/lDlQyl8T")

    assert "POLITICAL" in res.attributes


def test_classify_message_adds_political_for_speaker_johnson(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["PROMO"], "reason": "marketing"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()

    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("This is Speaker Johnson, urgent update")

    assert "POLITICAL" in res.attributes
    assert "PROMO" in res.attributes


def test_classify_message_adds_political_for_noisy_voter_id(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["UNKNOWN"], "reason": "unclear"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()

    noisy = "Do you support mandatory [Voter ID] or not?"
    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message(noisy)

    assert "POLITICAL" in res.attributes


def test_classify_message_adds_political_for_noisy_trump_brackets(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["SPAM"], "reason": "bulk"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()

    noisy = r"[Trump] just gave [MAGA\ the SILVER BULLET."
    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message(noisy)

    assert "POLITICAL" in res.attributes
    assert "SPAM" in res.attributes


def test_classify_message_adds_political_for_gop_bracket_noise(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["UNKNOWN"], "reason": "unclear"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()

    noisy = "I promised [GOP] Leadership Shawn would accept this"
    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message(noisy)

    assert "POLITICAL" in res.attributes


def test_classify_message_weights_drop_below_threshold(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps(
        {
            "attributes": ["POLITICAL", "SPAM"],
            "reason": "x",
            "weights": {"POLITICAL": 0.9, "SPAM": 0.2},
        }
    )
    raw = json.dumps(_anthropic_response_payload(inner)).encode()
    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("body")
    assert res.attributes == ["POLITICAL"]
    assert res.weights["SPAM"] == 0.2


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
