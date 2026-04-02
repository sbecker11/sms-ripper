# classifier.py
"""
Sends message text to Claude API and returns active attributes, reason, and per-tag weights.

**Vocabulary:** Allowed attribute names are whatever **active** tags exist in
:data:`tag_catalog` (SQLite ``sms_ripper_tag_catalog``), not a fixed enum in this module.
The list below matches the **default** ``tag_catalog.DEFAULT_TAG_ROWS``. Add more keys in the
DB catalog if you need finer topics; keep ``rules.py`` in sync.

Illustrative keys (default seed; all lowercase strings):
  education      — civic / campaign-style bulk SMS (PACs, parties); archive target for default rule
  church         — ward/stake or congregation bulk (programs, hymn schedules, meeting announcements)
  sofi           — SMS explicitly from SoFi (fraud/verify spend, account alerts); use ``transactional`` for generic banks
  personal       — conversation with a person you know
  transactional  — OTP/2FA, banks, shipping, appointments, order/receipt updates
  promo          — marketing and deals (rules use this key)
  social         — social-network / platform notifications and invites
  spam           — unsolicited junk, phishing, impersonation, or cold outreach (one bucket)
  stop           — opt-out or STOP-style intent
  unknown        — not enough signal (may be exclusive vs other tags when decoded)
"""

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import NamedTuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError

import config
import reader
import tag_catalog

# Tags with weight below this are dropped from rule-driving ``attributes`` when the model sends weights.
TAG_WEIGHT_THRESHOLD = 0.5

# When a tag is added or raised only via keyword heuristics (not the model), clamp weight to [lo, hi].
# Per-tag entries override these defaults for that tag only.
DEFAULT_KEYWORD_HEURISTIC_MIN_WEIGHT = 0.88
DEFAULT_KEYWORD_HEURISTIC_MAX_WEIGHT = 1.0
KEYWORD_HEURISTIC_MIN_WEIGHT_BY_TAG: dict[str, float] = {}
KEYWORD_HEURISTIC_MAX_WEIGHT_BY_TAG: dict[str, float] = {}


def _norm_attr(s: object) -> str:
    return tag_catalog.normalize_tag(str(s))


class ClassificationResult(NamedTuple):
    """Classifier output: tags that pass the weight threshold, free-text reason, all scored tags."""

    attributes: list[str]
    reason: str
    weights: dict[str, float]


def _build_system_prompt(active_tags: list[str]) -> str:
    tags = sorted({_norm_attr(t) for t in active_tags if str(t).strip()})
    if "unknown" not in tags:
        tags.append("unknown")
    tag_list = ", ".join(tags)
    return f"""You are a message classification agent.
Given an SMS or iMessage, return a JSON object with this exact shape:

{{
  "attributes": ["spam", "personal"],
  "reason": "brief explanation",
  "weights": {{"spam": 0.0, "personal": 0.0}}
}}

Available attributes (assign all that apply):
{tag_list}

Rules:
- Always assign at least one attribute.
- Use unknown when there is not enough signal.
- For every lowercase name in "attributes", include the same key in "weights" with a number from 0 to 1
  (supervised-style confidence: 1 = definite yes, 0 = no). Omitting "weights" is allowed; if present,
  every listed attribute must have a weight.
- Return ONLY the JSON object. No markdown, no preamble.
"""


def keyword_heuristic_weight_bounds(tag: str) -> tuple[float, float]:
    """
    Return ``(min, max)`` in [0, 1] used when ``tag`` is merged via :data:`KEYWORD_HEURISTIC_CHECKERS`.
    Per-tag overrides use :data:`KEYWORD_HEURISTIC_MIN_WEIGHT_BY_TAG` /
    :data:`KEYWORD_HEURISTIC_MAX_WEIGHT_BY_TAG`; missing keys use the defaults above.
    """
    t = _norm_attr(tag)
    lo = KEYWORD_HEURISTIC_MIN_WEIGHT_BY_TAG.get(t, DEFAULT_KEYWORD_HEURISTIC_MIN_WEIGHT)
    hi = KEYWORD_HEURISTIC_MAX_WEIGHT_BY_TAG.get(t, DEFAULT_KEYWORD_HEURISTIC_MAX_WEIGHT)
    lo = max(0.0, min(1.0, float(lo)))
    hi = max(0.0, min(1.0, float(hi)))
    if hi < lo:
        hi = lo
    return lo, hi


# Civic / PAC-style SMS markers → default catalog tag ``education``. Add more (tag, checker) pairs
# in :data:`KEYWORD_HEURISTIC_CHECKERS` and matching marker tuples for other tags as needed.
CIVIC_EDUCATION_KEYWORD_MARKERS: tuple[str, ...] = (
    "us-red",
    "white house",
    "oval office",
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
    "txt gop",
    "text gop",
    "republican races",
    "contact congress",
    "congress needs",
    "expel ",
    "from congress",
    "congress",
    "speaker gingrich",
    "vice president vance",
    "jd vance",
    "j.d. vance",
    "president trump",
    "pres. trump",
    "senator kennedy",
    "sen. kennedy",
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
    "fundgop.net",
    "rep2026.co",
    "usa-26.io",
    "us4u.io",
    "am1st.info",
    "speaker johnson",
    "marsha blackburn",
    "ted cruz",
    "senator cruz",
    "sen. cruz",
    "josh hawley",
    "senator hawley",
    "sen. hawley",
    "john thune",
    "senator thune",
    "sen. thune",
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
    "defunded",
    "defunding",
    "defund",
    "government",
    "nyc radicals",
    "nyc radical",
)

# Regex backstop for noisy civic SMS where punctuation/brackets vary.
CIVIC_EDUCATION_KEYWORD_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:house|senate)\s*gop\b", re.IGNORECASE),
    re.compile(r"\bpres(?:ident|\.)?\s*trump\b", re.IGNORECASE),
    re.compile(r"\boval\s+office\b", re.IGNORECASE),
    re.compile(r"\b(?:vp\s*)?jd\s*vance\b", re.IGNORECASE),
    re.compile(r"\bspeaker\s+johnson\b", re.IGNORECASE),
    re.compile(r"\bted\s+cruz\b", re.IGNORECASE),
    re.compile(r"\bsen(?:ator)?\.?\s+cruz\b", re.IGNORECASE),
    re.compile(r"\bjosh\s+hawley\b", re.IGNORECASE),
    re.compile(r"\bsen(?:ator)?\.?\s+hawley\b", re.IGNORECASE),
    re.compile(r"\bjohn\s+kennedy\b", re.IGNORECASE),
    re.compile(r"\bsen(?:ator)?\.?\s+kennedy\b", re.IGNORECASE),
    re.compile(r"\bjohn\s+thune\b", re.IGNORECASE),
    re.compile(r"\bsen(?:ator)?\.?\s+thune\b", re.IGNORECASE),
    re.compile(r"\bdefund(?:ed|ing|s)?\b", re.IGNORECASE),
    re.compile(r"\bgovernment\b", re.IGNORECASE),
    re.compile(r"\bnyc\s+radical(s)?\b", re.IGNORECASE),
    re.compile(r"\bcongress\b", re.IGNORECASE),
    re.compile(r"\bvoter\s*id\b", re.IGNORECASE),
    re.compile(r"\bproof\s+of\s+citizenship\b", re.IGNORECASE),
    re.compile(r"\b(?:dnc|rnc|dems?|gop|maga)\b", re.IGNORECASE),
    # G.O.P., G O P, or punctuation-stripped “g o p” after normalize
    re.compile(r"\bg\s*\.?\s*o\s*\.?\s*p\s*\.?\b", re.IGNORECASE),
    re.compile(r"\bg\s+o\s+p\b", re.IGNORECASE),
    re.compile(
        r"\b(?:housegop\.info|rnctxt\.co|redtxt\.vip|fundgop\.net|rep2026\.co|voterep\.co|26gop\.com|gop26\.info|us4u\.io)\b",
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


def _sofi_keyword_hit(text: str) -> bool:
    """True when body mentions SoFi (word boundary; case-insensitive)."""
    if not (text or "").strip():
        return False
    return bool(re.search(r"\bsofi\b", text, flags=re.IGNORECASE))


def _civic_education_keyword_hit(text: str) -> bool:
    raw = text.lower()
    norm = _normalize_for_keyword_scan(text)
    for phrase in CIVIC_EDUCATION_KEYWORD_MARKERS:
        if phrase in raw or phrase in norm:
            return True
    for pat in CIVIC_EDUCATION_KEYWORD_PATTERNS:
        if pat.search(text) or pat.search(norm):
            return True
    return False


# (catalog tag key, ``text -> bool``). If the model already listed the tag, skip (no weight bump).
KEYWORD_HEURISTIC_CHECKERS: tuple[tuple[str, Callable[[str], bool]], ...] = (
    ("education", _civic_education_keyword_hit),
    ("sofi", _sofi_keyword_hit),
)


def _merge_keyword_heuristics(
    text: str, attributes: list[str], weights: dict[str, float]
) -> tuple[list[str], dict[str, float]]:
    attr_set = {_norm_attr(a) for a in attributes}
    w = {_norm_attr(k): float(v) for k, v in weights.items()}
    attrs = [_norm_attr(a) for a in attributes]
    for tag_raw, checker in KEYWORD_HEURISTIC_CHECKERS:
        tag = _norm_attr(tag_raw)
        if not tag or tag in attr_set:
            continue
        if not checker(text):
            continue
        attrs = [*attrs, tag]
        attr_set.add(tag)
        lo, hi = keyword_heuristic_weight_bounds(tag)
        w[tag] = min(max(w.get(tag, 0.0), lo), hi)
    return attrs, w


def _normalize_weights_from_payload(
    raw: dict[str, object] | None, attributes: list[str]
) -> dict[str, float]:
    out: dict[str, float] = {}
    if raw:
        for k, v in raw.items():
            kk = _norm_attr(k)
            if not kk:
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            out[kk] = max(0.0, min(1.0, fv))
    for a in attributes:
        au = _norm_attr(a)
        if au:
            out.setdefault(au, 1.0)
    return out


def _active_attributes(
    attrs: list[str], weights: dict[str, float], had_explicit_weights: bool
) -> list[str]:
    if not had_explicit_weights:
        return attrs
    active = [a for a in attrs if weights.get(_norm_attr(a), 0.0) >= TAG_WEIGHT_THRESHOLD]
    return active if active else list(attrs)


class ClassificationPayload(BaseModel):
    """JSON shape returned in the assistant message text block."""

    model_config = ConfigDict(extra="ignore")

    attributes: list[str] = Field(default_factory=lambda: ["unknown"])
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
        attrs = [_norm_attr(x) for x in v if x is not None and str(x).strip()]
        attrs = [a for a in attrs if a]
        weights = {a: 1.0 for a in attrs}
        if "unknown" in attrs:
            return ["unknown"], {"unknown": 1.0}
        return attrs, weights
    if isinstance(v, dict):
        raw_attrs = v.get("attributes")
        raw_w = v.get("weights")
        if isinstance(raw_attrs, list):
            attrs = [
                _norm_attr(x) for x in raw_attrs if x is not None and str(x).strip()
            ]
            attrs = [a for a in attrs if a]
        else:
            attrs = []
        weights: dict[str, float] = {}
        if isinstance(raw_w, dict):
            for k, val in raw_w.items():
                kk = _norm_attr(k)
                if not kk:
                    continue
                try:
                    weights[kk] = max(0.0, min(1.0, float(val)))
                except (TypeError, ValueError):
                    pass
        for a in attrs:
            weights.setdefault(a, 1.0)
        if "unknown" in attrs:
            return ["unknown"], {"unknown": weights.get("unknown", 1.0)}
        return attrs, weights
    return [], {}


def encode_classifier_blob(attributes: list[str], weights: dict[str, float]) -> str:
    """JSON for ``classifier_attributes`` (includes weights for all active tags)."""
    attrs_u = [_norm_attr(a) for a in attributes if _norm_attr(a)]
    w = {_norm_attr(k): max(0.0, min(1.0, float(v))) for k, v in weights.items() if _norm_attr(k)}
    for a in attrs_u:
        w.setdefault(a, 1.0)
    return json.dumps({"attributes": attrs_u, "weights": w}, ensure_ascii=False)


def merge_tag_in_classifier_blob(raw: object, old_tag: str, new_tag: str) -> tuple[str | None, bool]:
    """
    Replace normalized ``old_tag`` with ``new_tag`` in a stored ``classifier_attributes`` value.
    Merges duplicate attributes and combines weights with ``max`` when both keys appear.
    Returns ``(new_json, True)`` when the blob should be rewritten, or ``(None, False)`` when
    unchanged or empty.
    """
    old_c = _norm_attr(old_tag)
    new_c = _norm_attr(new_tag)
    if not old_c or not new_c or old_c == new_c:
        return None, False
    attrs, weights = decode_classifier_blob(raw)
    w = {k: float(v) for k, v in weights.items()}
    had_old_attr = old_c in attrs
    w_old = w.pop(old_c, None)
    if not had_old_attr and w_old is None:
        return None, False
    w_new_existing = w.get(new_c)
    if w_old is not None:
        w[new_c] = max(w_new_existing or 0.0, w_old)
    elif w_new_existing is not None:
        w[new_c] = w_new_existing
    out_attrs: list[str] = []
    seen: set[str] = set()
    for a in attrs:
        b = new_c if a == old_c else a
        if b and b not in seen:
            out_attrs.append(b)
            seen.add(b)
    if new_c not in seen and (had_old_attr or w_old is not None):
        out_attrs.append(new_c)
        seen.add(new_c)
    for a in out_attrs:
        w.setdefault(a, 1.0)
    new_blob = encode_classifier_blob(out_attrs, w)
    prev = (
        None
        if raw is None
        else (
            raw.decode("utf-8", errors="replace").strip()
            if isinstance(raw, bytes)
            else str(raw).strip()
        )
    )
    if prev == new_blob.strip():
        return None, False
    return new_blob, True


def _has_no_usable_plaintext(text: str) -> bool:
    """
    True when there is nothing real to classify: empty/whitespace, or only
    :data:`reader.RICH_ONLY_PLACEHOLDER` line(s) with no subject or other line.
    """
    if not (text or "").strip():
        return True
    ph = reader.RICH_ONLY_PLACEHOLDER.strip()
    for ln in (text or "").splitlines():
        s = ln.strip()
        if not s:
            continue
        if s != ph:
            return False
    return True


def classify_message(
    text: str, *, human_guidance: str | None = None
) -> ClassificationResult:
    """
    Returns active attributes (weight >= :data:`TAG_WEIGHT_THRESHOLD` when weights are present),
    reason, and per-tag weights in ``[0, 1]``.

    When ``human_guidance`` is set, it is appended to the user message so reviewers can steer
    the model while keeping the same JSON output contract.

    Empty subject+body (no non-whitespace text), or body that is only the rich-content
    placeholder with no subject line, returns UNKNOWN without calling the API unless
    ``human_guidance`` is non-empty so training / reviewer hints can still be classified.
    """
    if _has_no_usable_plaintext(text):
        if not (human_guidance or "").strip():
            return ClassificationResult(
                ["unknown"],
                "no subject or body — nothing to classify",
                {"unknown": 1.0},
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

    active_tags = tag_catalog.active_tags_from_db(config.CHAT_DB_PATH)
    payload = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 256,
        "system": _build_system_prompt(active_tags),
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
        u_attrs, u_w = _merge_keyword_heuristics(text, ["unknown"], {"unknown": 1.0})
        if "unknown" in {_norm_attr(a) for a in u_attrs}:
            return ClassificationResult(
                ["unknown"],
                f"Could not parse response: {raw_text[:200]}",
                {"unknown": u_w.get("unknown", 1.0)},
            )
        return ClassificationResult(u_attrs, f"Could not parse response: {raw_text[:200]}", u_w)

    try:
        model = ClassificationPayload.model_validate(parsed)
    except ValidationError:
        u_attrs, u_w = _merge_keyword_heuristics(text, ["unknown"], {"unknown": 1.0})
        if "unknown" in {_norm_attr(a) for a in u_attrs}:
            return ClassificationResult(
                ["unknown"],
                f"Could not parse response: {raw_text[:200]}",
                {"unknown": u_w.get("unknown", 1.0)},
            )
        return ClassificationResult(u_attrs, f"Could not parse response: {raw_text[:200]}", u_w)

    attrs_norm = [_norm_attr(a) for a in model.attributes if _norm_attr(a)]
    had_explicit = model.weights is not None and len(model.weights) > 0
    weights = _normalize_weights_from_payload(
        dict(model.weights) if had_explicit else None,
        attrs_norm,
    )
    attrs_norm, weights = _merge_keyword_heuristics(text, attrs_norm, weights)

    # Enforce unknown exclusivity: if the classifier assigns unknown, drop all other tags.
    if "unknown" in attrs_norm:
        attrs_norm = ["unknown"]
        weights = {"unknown": weights.get("unknown", 1.0)}
    active = _active_attributes(attrs_norm, weights, had_explicit)
    return ClassificationResult(active, model.reason, weights)
