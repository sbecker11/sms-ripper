# classifier.py
"""
Sends message text to Claude API and returns a list of attributes.

Possible attributes (extend as needed):
  SPAM       — unsolicited commercial or phishing message
  STOP       — opt-out / unsubscribe request or trigger
  SCAM       — fraud / impersonation attempt
  POLITICAL  — political messaging
  PROMO      — promotional but not necessarily spam
  LEGIT      — legitimate message, no action needed
  PERSONAL   — from a known person
  UNKNOWN    — cannot determine
"""

import json
import urllib.error
import urllib.request

from pydantic import BaseModel, ConfigDict, Field, ValidationError

import config

SYSTEM_PROMPT = """You are a message classification agent. 
Given an SMS or iMessage, return a JSON object with this exact shape:

{
  "attributes": ["ATTR1", "ATTR2"],
  "reason": "brief explanation"
}

Available attributes (assign all that apply):
- SPAM: unsolicited bulk/commercial message
- STOP: message is an opt-out, unsubscribe, or the word STOP itself
- SCAM: fraud, phishing, impersonation, fake prize, fake package delivery
- POLITICAL: political campaign or polling message; also if the text contains **us-red** or **White House** (case-insensitive)
- PROMO: promotional offer from a real business (not spam)
- LEGIT: clearly legitimate transactional message (bank OTP, delivery confirmation, appointment reminder)
- PERSONAL: appears to be from a real known person
- UNKNOWN: cannot determine

Rules:
- Always assign at least one attribute.
- SPAM and SCAM can coexist.
- LEGIT and SPAM cannot coexist.
- Return ONLY the JSON object. No markdown, no preamble.
"""


# If any phrase appears in the message (case-insensitive), POLITICAL is merged into attributes.
POLITICAL_TEXT_MARKERS: tuple[str, ...] = ("us-red", "white house")


def _merge_political_keywords(text: str, attributes: list[str]) -> list[str]:
    if "POLITICAL" in attributes:
        return attributes
    blob = text.lower()
    for phrase in POLITICAL_TEXT_MARKERS:
        if phrase in blob:
            return [*attributes, "POLITICAL"]
    return attributes


class ClassificationPayload(BaseModel):
    """JSON shape returned in the assistant message text block."""

    model_config = ConfigDict(extra="ignore")

    attributes: list[str] = Field(default_factory=lambda: ["UNKNOWN"])
    reason: str = ""


def classify_message(text: str) -> tuple[list[str], str]:
    """
    Returns (attributes, reason) for a given message text.
    Falls back to ["UNKNOWN"] on any API error.
    """
    if not config.ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY is not set in .env (project root).")

    payload = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 256,
        "system": SYSTEM_PROMPT,
        "messages": [
            {"role": "user", "content": f"Classify this message:\n\n{text}"}
        ]
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": config.ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"Claude API error {e.code}: {body}")

    raw_text = ""
    for block in data.get("content", []):
        if block.get("type") == "text":
            raw_text += block["text"]

    try:
        parsed = json.loads(raw_text.strip())
    except json.JSONDecodeError:
        attrs = _merge_political_keywords(text, ["UNKNOWN"])
        return attrs, f"Could not parse response: {raw_text[:200]}"

    try:
        model = ClassificationPayload.model_validate(parsed)
    except ValidationError:
        attrs = _merge_political_keywords(text, ["UNKNOWN"])
        return attrs, f"Could not parse response: {raw_text[:200]}"

    attributes = _merge_political_keywords(text, [a.upper() for a in model.attributes])
    return attributes, model.reason
