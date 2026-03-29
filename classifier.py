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
import reader

SYSTEM_PROMPT = """You are a message classification agent.
Given an SMS or iMessage, return a JSON object with this exact shape:

{
  "attributes": ["ATTR1", "ATTR2"],
  "reason": "brief explanation"
}

Available attributes (assign all that apply):
- SPAM: unsolicited bulk/commercial message (many political SMS count as both SPAM and POLITICAL)
- STOP: message is an opt-out, unsubscribe, or the word STOP itself
- SCAM: fraud, phishing, impersonation, fake prize, fake package delivery
- POLITICAL: partisan or civic political content — campaigns, PACs, party committees, fundraising,
  petitions, surveys, "contact your representative", get-out-the-vote, named politicians or
  federal offices (President, VP, Speaker, Senator, Congress, White House, Supreme Court in a
  civic/policy sense), party labels (GOP, Republican, Democrat, DNC, RNC), or links/domains
  typical of political texting (e.g. vote-red, win-red, redtxt, short .red links, "save america").
  Use POLITICAL even if the message also feels like spam or promo.
- PROMO: promotional offer from a real business (not spam)
- LEGIT: clearly legitimate transactional message (bank OTP, delivery confirmation, appointment reminder)
- PERSONAL: one-to-one message from someone you know — NOT bulk political texts that insert a first name
- UNKNOWN: cannot determine

Rules:
- Always assign at least one attribute.
- SPAM and SCAM can coexist.
- LEGIT and SPAM cannot coexist.
- Political fundraising / party blast SMS: include POLITICAL; add SPAM if unsolicited bulk.
- Return ONLY the JSON object. No markdown, no preamble.
"""


# If any substring appears in the message (case-insensitive), POLITICAL is merged after the model
# returns (catches common PAC / party SMS the model still misses).
POLITICAL_TEXT_MARKERS: tuple[str, ...] = (
    "us-red",
    "white house",
    "vote-red",
    "win-red",
    "redtxt",
    "clkred",
    "txtred",
    "righttxt",
    ".red/",
    "maga",
    "house gop",
    "senate gop",
    "critical house",
    "save america act",
    "voter id",
    "voter verification",
    "gop races",
    "republican races",
    "contact congress",
    "congress needs",
    "expel ",
    "from congress",
    "speaker gingrich",
    "vice president vance",
    "jd vance",
    "j.d. vance",
    "president trump",
    "pres. trump",
    "senator kennedy",
    "john kennedy",
    "juan ciscomani",
    "tulsi gabbard",
    "supreme court ruling",
    "ban china",
    "u.s. farmland",
    "i.c.e. tumbler",
)


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
    if text == reader.RICH_ONLY_PLACEHOLDER:
        # No plaintext in chat.db; API would see the same prompt for every row. Assume bulk SMS.
        return (
            ["POLITICAL", "SPAM"],
            "attributedBody only in chat.db — heuristic POLITICAL+SPAM (no API)",
        )

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
