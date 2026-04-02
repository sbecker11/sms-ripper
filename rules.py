# rules.py
"""
Rules engine: maps message attributes → list of actions to take.

**Policies** (string switches in code, not tags in the catalog): ``political`` and ``spam``
are rule-set names only. **Tags** (e.g. ``education``, ``spam``, ``religion``) are whatever
keys you define in ``tag_catalog``; the conditions below use this repo’s **default** seed names
as examples.

Policies (see evaluate_detailed policy=):
  political — user outbound STOP-only (``STOP_REPLY_TEXT``) → delete thread; purge unsubscribe confirmations; archive ``church`` / ``sofi`` / ``education`` when non-personal; no STOP/block.
  spam      — user outbound STOP-only → delete thread; inbound SPAM/STOP → send_stop / block / delete; legacy ``scam``-only rows; no political rule (run second).

Actions:
  send_stop    — reply with STOP text
  block        — block the sender in Messages
  delete       — delete the entire chat thread
  archive      — copy message row into <tag>_archive in chat.db, then remove the live row
  purge        — remove the live message row only (no *_archive copy)
  log_only     — just log it, no action
"""

from dataclasses import dataclass
from typing import Callable, Literal

import tag_catalog
from reader import Message, plain_text_is_user_stop_command

Policy = Literal["political", "spam"]


def _tags(m: Message) -> set[str]:
    return {tag_catalog.normalize_tag(a) for a in (m.attributes or []) if str(a).strip()}


def _outbound_user_stop_delete(m: Message) -> bool:
    """True when this row is from the user and body/subject is only the configured STOP text."""
    return m.is_from_me and plain_text_is_user_stop_command(m.combined_plaintext())


def text_looks_like_unsubscribe_confirmation(m: Message) -> bool:
    """
    Carrier/campaign auto-replies after STOP (often delayed minutes or hours).

    Uses ``Message.combined_plaintext()`` (MMS **subject** + **body**). If both contain
    e.g. "You have been unsubscribed", the merged string still matches once → one ``purge``.

    Those messages only get purged when they fall inside the run's lookback window
    (e.g. `poe political-all` uses a wide window). Re-run later if a confirmation
    just arrived. Case-insensitive substring match.
    """
    t = m.combined_plaintext().lower()
    needles = (
        "you have unsubscribed",
        "you've unsubscribed",
        "you have been unsubscribed",
        "you've been unsubscribed",
        "successfully unsubscribed",
        "were unsubscribed",
        "you are unsubscribed",
        "has been unsubscribed",
        "been unsubscribed",
        "unsubscribe confirmed",
        "opt-out confirmed",
        "opt out confirmed",
        "you have been removed",
        "removed from our mailing list",
        "removed from this list",
        "no longer receive messages",
        "subscription has been cancelled",
        "subscription has been canceled",
    )
    return any(n in t for n in needles)


@dataclass
class Rule:
    name: str
    description: str
    condition: Callable[[Message], bool]
    actions: list[str]


# --- political first ---

RULES_POLITICAL: list[Rule] = [
    Rule(
        name="outbound_user_stop_delete",
        description="User sent STOP (or STOP_REPLY_TEXT) alone — delete thread in Messages",
        condition=_outbound_user_stop_delete,
        actions=["delete"],
    ),
    Rule(
        name="unsubscribe_confirmation",
        description="Unsubscribe confirmation text — remove from thread, do not archive",
        condition=lambda m: text_looks_like_unsubscribe_confirmation(m),
        actions=["purge"],
    ),
    Rule(
        name="church_announcement",
        description="Church / ward bulk (programs, sacrament notes) — copy to church_archive",
        condition=lambda m: "church" in _tags(m)
        and "personal" not in _tags(m)
        and not text_looks_like_unsubscribe_confirmation(m),
        actions=["archive"],
    ),
    Rule(
        name="sofi_brand",
        description="SoFi SMS (verify spend, alerts) — copy to sofi_archive",
        condition=lambda m: "sofi" in _tags(m)
        and "personal" not in _tags(m)
        and not text_looks_like_unsubscribe_confirmation(m),
        actions=["archive"],
    ),
    Rule(
        name="political",
        description="Civic / PAC bulk — copy to message_tags_archive and remove from live message table",
        condition=lambda m: "education" in _tags(m)
        and "personal" not in _tags(m)
        and not text_looks_like_unsubscribe_confirmation(m),
        actions=["archive"],
    ),
    Rule(
        name="promo_only",
        description="Promotional but not spam — log only",
        condition=lambda m: "promo" in _tags(m) and "spam" not in _tags(m),
        actions=["log_only"],
    ),
    Rule(
        name="personal",
        description="Personal message — no action",
        condition=lambda m: "personal" in _tags(m),
        actions=[],
    ),
]

# --- spam second: no political rule (``education`` rows should already be archived) ---

RULES_SPAM: list[Rule] = [
    Rule(
        name="outbound_user_stop_delete",
        description="User sent STOP alone — delete thread (do not send_stop to self)",
        condition=_outbound_user_stop_delete,
        actions=["delete"],
    ),
    Rule(
        name="spam_stop",
        description="Inbound message is SPAM and/or STOP — reply STOP, block, delete",
        condition=lambda m: bool({"spam", "stop"} & _tags(m)) and not _outbound_user_stop_delete(m),
        actions=["send_stop", "block", "delete"],
    ),
    Rule(
        name="legacy_scam_only",
        description="Legacy SCAM tag (removed from default catalog) — block and delete without STOP",
        condition=lambda m: "scam" in _tags(m)
        and "spam" not in _tags(m)
        and "stop" not in _tags(m),
        actions=["block", "delete"],
    ),
    Rule(
        name="promo_only",
        description="Promotional but not spam — log only",
        condition=lambda m: "promo" in _tags(m) and "spam" not in _tags(m),
        actions=["log_only"],
    ),
    Rule(
        name="personal",
        description="Personal message — no action",
        condition=lambda m: "personal" in _tags(m),
        actions=[],
    ),
]

# Back-compat alias: default agent policy is political
RULES: list[Rule] = RULES_POLITICAL


def rules_for_policy(policy: str) -> list[Rule]:
    if policy == "spam":
        return RULES_SPAM
    return RULES_POLITICAL


def evaluate_detailed(
    message: Message, *, policy: Policy | str = "political"
) -> tuple[list[str], list[str]]:
    """
    Returns (merged_actions, matched_rule_names) in rule order.
    If no rule matches, actions become ["log_only"] and matched names stay empty.
    """
    rule_list = rules_for_policy(policy if policy in ("political", "spam") else "political")
    all_actions: list[str] = []
    matched_rules: list[str] = []

    for rule in rule_list:
        if rule.condition(message):
            matched_rules.append(rule.name)
            for action in rule.actions:
                if action not in all_actions:
                    all_actions.append(action)

    if not all_actions and not matched_rules:
        all_actions = ["log_only"]

    return all_actions, matched_rules


def evaluate(message: Message, *, policy: Policy | str = "political") -> list[str]:
    """
    Returns the merged list of actions for all matching rules, deduplicated
    in rule order. If no rule matches, returns ["log_only"].
    """
    actions, _ = evaluate_detailed(message, policy=policy)
    return actions
