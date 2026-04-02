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
    inner = json.dumps({"attributes": ["personal"], "reason": "ok"})
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
    assert res.attributes == ["personal"]
    assert res.reason == "ok"
    assert res.weights.get("personal") == 1.0


def test_classify_message_includes_human_guidance_in_request(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["personal"], "reason": "ok"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()
    captured: list[dict] = []

    def capture_urlopen(req, timeout=None):
        captured.append(json.loads(req.data.decode()))
        return _FakeResponse(raw)

    with patch.object(classifier.urllib.request, "urlopen", side_effect=capture_urlopen):
        classifier.classify_message(
            "plain body",
            human_guidance="Human says treat as personal.",
        )
    assert captured
    user = captured[0]["messages"][0]["content"]
    assert "plain body" in user
    assert "Human reviewer guidance" in user
    assert "Human says treat as personal." in user


def test_classify_message_parses_json(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["spam", "stop"], "reason": "bulk sms"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()

    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("WINNER click now")

    assert res.attributes == ["spam", "stop"]
    assert res.reason == "bulk sms"


def test_classify_message_invalid_payload_shape_returns_unknown(monkeypatch):
    """Valid JSON but wrong types for ClassificationPayload → ValidationError path."""
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": "not-a-list", "reason": "x"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()

    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("hello")

    assert res.attributes == ["unknown"]
    assert "Could not parse" in res.reason


def test_classify_message_malformed_inner_json_returns_unknown(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = "not json {"
    raw = json.dumps(_anthropic_response_payload(inner)).encode()

    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("hello")

    assert res.attributes == ["unknown"]
    assert "Could not parse" in res.reason


def test_classify_message_missing_api_key():
    with patch.object(config, "ANTHROPIC_API_KEY", ""):
        with pytest.raises(ValueError, match=r"ANTHROPIC_API_KEY.*\.env"):
            classifier.classify_message("hi")


def test_classify_message_rich_placeholder_only_skips_http_unknown():
    """Rich-only placeholder with no subject line is not education+spam; same as empty body."""
    with patch.object(classifier.urllib.request, "urlopen") as urlopen_mock:
        res = classifier.classify_message(reader.RICH_ONLY_PLACEHOLDER)
    urlopen_mock.assert_not_called()
    assert res.attributes == ["unknown"]
    assert "no subject or body" in res.reason
    assert res.weights == {"unknown": 1.0}


def test_classify_message_subject_plus_rich_placeholder_calls_api(monkeypatch):
    """MMS subject + placeholder body still has usable plaintext for the model."""
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["personal"], "reason": "ok"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()
    combined = f"Shipped\n{reader.RICH_ONLY_PLACEHOLDER}"
    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)) as m:
        res = classifier.classify_message(combined)
    m.assert_called_once()
    assert res.attributes == ["personal"]


def test_classify_message_empty_text_returns_unknown_skips_http():
    """No subject/body: unknown without API; works even when ANTHROPIC_API_KEY is unset."""
    with patch.object(config, "ANTHROPIC_API_KEY", ""):
        with patch.object(classifier.urllib.request, "urlopen") as urlopen_mock:
            res = classifier.classify_message("")
    urlopen_mock.assert_not_called()
    assert res.attributes == ["unknown"]
    assert "no subject or body" in res.reason
    assert res.weights == {"unknown": 1.0}

    with patch.object(classifier.urllib.request, "urlopen") as urlopen_mock:
        res2 = classifier.classify_message("   \n\t  ")
    urlopen_mock.assert_not_called()
    assert res2.attributes == ["unknown"]


def test_classify_message_empty_text_with_human_guidance_still_calls_api(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["personal"], "reason": "from guidance"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()
    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)) as m:
        res = classifier.classify_message("", human_guidance="one-to-one from bank")
    m.assert_called_once()
    assert res.attributes == ["personal"]


def test_classify_message_adds_sofi_for_brand_text(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["transactional"], "reason": "bank alert"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()
    body = (
        "from SoFi. Did you try to spend $54.99 at GENERALGOODIES? Reply YES or NO within 1 hour"
    )
    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message(body)
    assert "sofi" in res.attributes
    assert "transactional" in res.attributes
    lo, hi = classifier.keyword_heuristic_weight_bounds("sofi")
    assert lo <= res.weights["sofi"] <= hi


def test_classify_message_adds_political_for_white_house(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["personal"], "reason": "ok"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()

    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("News from the White House")

    assert "education" in res.attributes
    assert "personal" in res.attributes
    lo, hi = classifier.keyword_heuristic_weight_bounds("education")
    assert lo <= res.weights["education"] <= hi


def test_classify_message_adds_political_for_us_red(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["promo"], "reason": "offer"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()

    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("Text us-red for updates")

    assert "education" in res.attributes


def test_classify_message_adds_political_for_vote_red_domain(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["spam"], "reason": "bulk"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()

    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("Vote now https://vote-red.io/tg9ri9")

    assert "education" in res.attributes
    assert "spam" in res.attributes


def test_classify_message_adds_political_for_ted_cruz_whitespace(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["promo"], "reason": "x"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()
    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("Message from Ted  Cruz — donate now")
    assert "education" in res.attributes


def test_classify_message_adds_political_for_oval_office(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["promo"], "reason": "x"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()
    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("From the Oval  Office — urgent")
    assert "education" in res.attributes


def test_classify_message_adds_political_for_congress(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["promo"], "reason": "x"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()
    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("Congress must act — donate now")
    assert "education" in res.attributes


def test_classify_message_adds_political_for_nyc_radicals(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["promo"], "reason": "x"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()
    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("Stop NYC  radicals — donate")
    assert "education" in res.attributes


def test_classify_message_adds_political_for_government(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["promo"], "reason": "x"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()
    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("Big Government is the problem — reply YES")
    assert "education" in res.attributes


def test_classify_message_adds_political_for_defunded(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["promo"], "reason": "x"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()
    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("They DEFUNDED border security — act now")
    assert "education" in res.attributes


def test_classify_message_adds_political_for_john_kennedy(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["promo"], "reason": "x"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()
    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("John  Kennedy: Louisiana update")
    assert "education" in res.attributes


def test_classify_message_adds_political_for_sen_kennedy(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["promo"], "reason": "x"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()
    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("Sen. Kennedy needs 500 patriots")
    assert "education" in res.attributes


def test_classify_message_adds_political_for_john_thune(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["promo"], "reason": "x"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()
    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("John  Thune: GOP leadership update")
    assert "education" in res.attributes


def test_classify_message_adds_political_for_senator_thune(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["promo"], "reason": "x"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()
    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("Sen. Thune here — reply YES")
    assert "education" in res.attributes


def test_classify_message_adds_political_for_josh_hawley(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["promo"], "reason": "x"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()
    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("Josh  Hawley: urgent PAC update")
    assert "education" in res.attributes


def test_classify_message_adds_political_for_senator_hawley(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["promo"], "reason": "x"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()
    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("Senator Hawley here — reply YES")
    assert "education" in res.attributes


def test_classify_message_adds_political_for_senator_cruz(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["promo"], "reason": "x"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()
    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("Sen. Cruz needs your help")
    assert "education" in res.attributes


def test_classify_message_adds_political_for_gop_abbreviation(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["promo"], "reason": "blast"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()
    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("Support the G.O.P. — reply YES")
    assert "education" in res.attributes


def test_classify_message_adds_political_for_txt_gop(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["promo"], "reason": "short code"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()
    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("Txt GOP to 80810 for updates")
    assert "education" in res.attributes


def test_classify_message_adds_political_for_us4u_domain(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["promo"], "reason": "offer"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()
    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("Tap https://us4u.io/abc123")
    assert "education" in res.attributes


def test_classify_message_adds_political_for_rep2026_domain(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["promo"], "reason": "offer"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()
    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("Sign: https://rep2026.co/abc")
    assert "education" in res.attributes


def test_classify_message_adds_political_for_fundgop_domain(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["personal"], "reason": "ok"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()
    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("Donate https://fundgop.net/xYz")
    assert "education" in res.attributes


def test_classify_message_adds_political_for_housegop_domain(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["unknown"], "reason": "unclear"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()

    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("Complete here: https://housegop.info/lDlQyl8T")

    assert res.attributes == ["unknown"]


def test_classify_message_adds_political_for_speaker_johnson(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["promo"], "reason": "marketing"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()

    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("This is Speaker Johnson, urgent update")

    assert "education" in res.attributes
    assert "promo" in res.attributes


def test_classify_message_adds_political_for_noisy_voter_id(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["unknown"], "reason": "unclear"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()

    noisy = "Do you support mandatory [Voter ID] or not?"
    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message(noisy)

    assert res.attributes == ["unknown"]


def test_classify_message_adds_political_for_noisy_trump_brackets(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["spam"], "reason": "bulk"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()

    noisy = r"[Trump] just gave [MAGA\ the SILVER BULLET."
    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message(noisy)

    assert "education" in res.attributes
    assert "spam" in res.attributes


def test_classify_message_adds_political_for_gop_bracket_noise(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps({"attributes": ["unknown"], "reason": "unclear"})
    raw = json.dumps(_anthropic_response_payload(inner)).encode()

    noisy = "I promised [GOP] Leadership Shawn would accept this"
    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message(noisy)

    assert res.attributes == ["unknown"]


def test_classify_message_malformed_inner_json_unknown_exclusive(monkeypatch):
    """If the model response is malformed, political keyword merge may occur — unknown must remain exclusive."""
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = "not json {"
    raw = json.dumps(_anthropic_response_payload(inner)).encode()
    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("News from the White House")
    assert res.attributes == ["unknown"]


def test_classify_message_weights_drop_below_threshold(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    inner = json.dumps(
        {
            "attributes": ["education", "spam"],
            "reason": "x",
            "weights": {"education": 0.9, "spam": 0.2},
        }
    )
    raw = json.dumps(_anthropic_response_payload(inner)).encode()
    with patch.object(classifier.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
        res = classifier.classify_message("body")
    assert res.attributes == ["education"]
    assert res.weights["spam"] == 0.2


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
