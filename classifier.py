# classifier.py
"""
Sends message text to Claude API and returns active attributes, reason, and per-tag weights.

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
import re
import urllib.error
import urllib.request
from typing import NamedTuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError

import config
import reader

# Tags with weight below this are dropped from rule-driving ``attributes`` when the model sends weights.
TAG_WEIGHT_THRESHOLD = 0.5
# When POLITICAL is added only from keyword heuristics (not the model), use this score.
HEURISTIC_POLITICAL_WEIGHT = 0.88


class ClassificationResult(NamedTuple):
    """Classifier output: tags that pass the weight threshold, free-text reason, all scored tags."""

    attributes: list[str]
    reason: str
    weights: dict[str, float]


SYSTEM_PROMPT = """You are a message classification agent.
Given an SMS or iMessage, return a JSON object with this exact shape:

{
  "attributes": ["ATTR1", "ATTR2"],
  "reason": "brief explanation",
  "weights": {"ATTR1": 0.0, "ATTR2": 0.0}
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
- For every name in "attributes", include the same key in "weights" with a number from 0 to 1
  (supervised-style confidence: 1 = definite yes, 0 = no). Omitting "weights" is allowed; if present,
  every listed attribute must have a weight.
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
    "housegop.info",
    "rnctxt.co",
    "win-26.org",
    "win-gop.io",
    "gop-1.com",
    "red-1st.com",
    "redtxt.vip",
    "voterep.co",
    "rep-26.com",
    "26gop.com",
    "gop26.info",
    "rep2026.co",
    "usa-26.io",
    "am1st.info",
    "speaker johnson",
    "marsha blackburn",
    "ted cruz",
    "donald trump jr",
    "steve scalise",
    "karoline leavitt",
    "hakeem jeffries",
    "chuck schumer",
    "derrick van orden",
    "vivek",
    "maga supporter status",
    "mandatory voter id",
    "proof of citizenship",
)

# Regex backstop for noisy political SMS where punctuation/brackets vary.
POLITICAL_TEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:house|senate)\s*gop\b", re.IGNORECASE),
    re.compile(r"\bpres(?:ident|\.)?\s*trump\b", re.IGNORECASE),
    re.compile(r"\b(?:vp\s*)?jd\s*vance\b", re.IGNORECASE),
    re.compile(r"\bspeaker\s+johnson\b", re.IGNORECASE),
    re.compile(r"\bvoter\s*id\b", re.IGNORECASE),
    re.compile(r"\bproof\s+of\s+citizenship\b", re.IGNORECASE),
    re.compile(r"\b(?:dnc|rnc|dems?|gop|maga)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:housegop\.info|rnctxt\.co|redtxt\.vip|rep2026\.co|voterep\.co|26gop\.com|gop26\.info)\b",
        re.IGNORECASE,
    ),
)


def _normalize_for_keyword_scan(text: str) -> str:
    """
    Normalize noisy SMS formatting (brackets/slashes/punctuation) for marker matching.
    Keeps dots for domains while flattening other separators to spaces.
    """
    s = text.lower()
    s = s.replace("\\", " ")
    s = re.sub(r"[\[\]\(\)\{\}\"“”'`~*_]+", " ", s)
    s = re.sub(r"[^a-z0-9\.\s:/-]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _looks_political_by_keyword(text: str) -> bool:
    raw = text.lower()
    norm = _normalize_for_keyword_scan(text)
    for phrase in POLITICAL_TEXT_MARKERS:
        if phrase in raw or phrase in norm:
            return True
    for pat in POLITICAL_TEXT_PATTERNS:
        if pat.search(text) or pat.search(norm):
            return True
    return False


def _merge_political_keywords(
    text: str, attributes: list[str], weights: dict[str, float]
) -> tuple[list[str], dict[str, float]]:
    attr_set = {a.upper() for a in attributes}
    w = dict(weights)
    if "POLITICAL" in attr_set:
        return attributes, w
    if _looks_political_by_keyword(text):
        if "POLITICAL" not in attr_set:
            attributes = [*attributes, "POLITICAL"]
        w["POLITICAL"] = max(w.get("POLITICAL", 0.0), HEURISTIC_POLITICAL_WEIGHT)
    return attributes, w


def _normalize_weights_from_payload(
    raw: dict[str, object] | None, attributes: list[str]
) -> dict[str, float]:
    out: dict[str, float] = {}
    if raw:
        for k, v in raw.items():
            kk = str(k).strip().upper()
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            out[kk] = max(0.0, min(1.0, fv))
    for a in attributes:
        au = a.upper()
        out.setdefault(au, 1.0)
    return out


def _active_attributes(
    attrs: list[str], weights: dict[str, float], had_explicit_weights: bool
) -> list[str]:
    if not had_explicit_weights:
        return attrs
    active = [a for a in attrs if weights.get(a.upper(), 0.0) >= TAG_WEIGHT_THRESHOLD]
    return active if active else list(attrs)


class ClassificationPayload(BaseModel):
    """JSON shape returned in the assistant message text block."""

    model_config = ConfigDict(extra="ignore")

    attributes: list[str] = Field(default_factory=lambda: ["UNKNOWN"])
    reason: str = ""
    weights: dict[str, float] | None = None


def decode_classifier_blob(raw: object) -> tuple[list[str], dict[str, float]]:
    """
    Parse ``classifier_attributes`` column: legacy JSON list or ``{"attributes":[],"weights":{}}``.
    """
    if raw is None:
        return [], {}
    if isinstance(raw, bytes):
        s = raw.decode("utf-8", errors="replace").strip()
    else:
        s = str(raw).strip()
    if not s:
        return [], {}
    try:
        v = json.loads(s)
    except (TypeError, ValueError):
        return [], {}
    if isinstance(v, list):
        attrs = [str(x).strip().upper() for x in v if x is not None and str(x).strip()]
        return attrs, {a: 1.0 for a in attrs}
    if isinstance(v, dict):
        raw_attrs = v.get("attributes")
        raw_w = v.get("weights")
        if isinstance(raw_attrs, list):
            attrs = [
                str(x).strip().upper() for x in raw_attrs if x is not None and str(x).strip()
            ]
        else:
            attrs = []
        weights: dict[str, float] = {}
        if isinstance(raw_w, dict):
            for k, val in raw_w.items():
                kk = str(k).strip().upper()
                try:
                    weights[kk] = max(0.0, min(1.0, float(val)))
                except (TypeError, ValueError):
                    pass
        for a in attrs:
            weights.setdefault(a, 1.0)
        return attrs, weights
    return [], {}


def encode_classifier_blob(attributes: list[str], weights: dict[str, float]) -> str:
    """JSON for ``classifier_attributes`` (includes weights for all active tags)."""
    attrs_u = [a.upper() for a in attributes]
    w = {str(k).upper(): max(0.0, min(1.0, float(v))) for k, v in weights.items()}
    for a in attrs_u:
        w.setdefault(a, 1.0)
    return json.dumps({"attributes": attrs_u, "weights": w}, ensure_ascii=False)


def classify_message(
    text: str, *, human_guidance: str | None = None
) -> ClassificationResult:
    """
    Returns active attributes (weight >= :data:`TAG_WEIGHT_THRESHOLD` when weights are present),
    reason, and per-tag weights in ``[0, 1]``.

    When ``human_guidance`` is set, it is appended to the user message so reviewers can steer
    the model while keeping the same JSON output contract.

    Empty subject+body (no non-whitespace text) returns UNKNOWN without calling the API unless
    ``human_guidance`` is non-empty so training / reviewer hints can still be classified.
    """
    if text == reader.RICH_ONLY_PLACEHOLDER:
        # No plaintext in chat.db; API would see the same prompt for every row. Assume bulk SMS.
        return ClassificationResult(
            ["POLITICAL", "SPAM"],
            "attributedBody only in chat.db — heuristic POLITICAL+SPAM (no API)",
            {"POLITICAL": 1.0, "SPAM": 1.0},
        )

    if not (text or "").strip():
        if not (human_guidance or "").strip():
            return ClassificationResult(
                ["UNKNOWN"],
                "no subject or body — nothing to classify",
                {"UNKNOWN": 1.0},
            )

    if not config.ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY is not set in .env (project root).")

    user_content = f"Classify this message:\n\n{text}"
    g = (human_guidance or "").strip()
    if g:
        user_content += (
            "\n\nHuman reviewer guidance (consider carefully; still apply all attribute rules "
            "and return only valid JSON with the required shape):\n"
            f"{g}"
        )

    payload = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 256,
        "system": SYSTEM_PROMPT,
        "messages": [
            {"role": "user", "content": user_content}
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
        u_attrs, u_w = _merge_political_keywords(text, ["UNKNOWN"], {"UNKNOWN": 1.0})
        return ClassificationResult(u_attrs, f"Could not parse response: {raw_text[:200]}", u_w)

    try:
        model = ClassificationPayload.model_validate(parsed)
    except ValidationError:
        u_attrs, u_w = _merge_political_keywords(text, ["UNKNOWN"], {"UNKNOWN": 1.0})
        return ClassificationResult(u_attrs, f"Could not parse response: {raw_text[:200]}", u_w)

    attrs_upper = [a.upper() for a in model.attributes]
    had_explicit = model.weights is not None and len(model.weights) > 0
    weights = _normalize_weights_from_payload(
        dict(model.weights) if had_explicit else None,
        attrs_upper,
    )
    attrs_upper, weights = _merge_political_keywords(text, attrs_upper, weights)
    active = _active_attributes(attrs_upper, weights, had_explicit)
    return ClassificationResult(active, model.reason, weights)
