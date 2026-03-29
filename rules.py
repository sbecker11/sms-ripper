# rules.py
"""
Rules engine: maps message attributes → list of actions to take.

Policies (see evaluate_detailed policy=):
  political — purge unsubscribe confirmations (subject or body text); archive POLITICAL (non-personal); no STOP/block.
  spam      — send_stop / block / delete for SPAM or STOP; SCAM rule; no political rule (run second).

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

from reader import Message

Policy = Literal["political", "spam"]


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
        name="unsubscribe_confirmation",
        description="Unsubscribe confirmation text — remove from thread, do not archive",
        condition=lambda m: text_looks_like_unsubscribe_confirmation(m),
        actions=["purge"],
    ),
    Rule(
        name="political",
        description="Political messaging — copy to POLITICAL_archive and remove from live message table",
        condition=lambda m: "POLITICAL" in m.attributes
        and "PERSONAL" not in m.attributes
        and not text_looks_like_unsubscribe_confirmation(m),
        actions=["archive"],
    ),
    Rule(
        name="promo_only",
        description="Promotional but not spam — log only",
        condition=lambda m: "PROMO" in m.attributes and "SPAM" not in m.attributes,
        actions=["log_only"],
    ),
    Rule(
        name="legit",
        description="Legitimate message — no action",
        condition=lambda m: "LEGIT" in m.attributes,
        actions=[],
    ),
    Rule(
        name="personal",
        description="Personal message — no action",
        condition=lambda m: "PERSONAL" in m.attributes,
        actions=[],
    ),
]

# --- spam second: no political rule (POLITICAL rows should already be archived) ---

RULES_SPAM: list[Rule] = [
    Rule(
        name="spam_stop",
        description="Message is SPAM and/or STOP — reply STOP, block, delete",
        condition=lambda m: bool({"SPAM", "STOP"} & set(m.attributes)),
        actions=["send_stop", "block", "delete"],
    ),
    Rule(
        name="scam",
        description="Message is a SCAM — block and delete without replying",
        condition=lambda m: "SCAM" in m.attributes,
        actions=["block", "delete"],
    ),
    Rule(
        name="promo_only",
        description="Promotional but not spam — log only",
        condition=lambda m: "PROMO" in m.attributes and "SPAM" not in m.attributes,
        actions=["log_only"],
    ),
    Rule(
        name="legit",
        description="Legitimate message — no action",
        condition=lambda m: "LEGIT" in m.attributes,
        actions=[],
    ),
    Rule(
        name="personal",
        description="Personal message — no action",
        condition=lambda m: "PERSONAL" in m.attributes,
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
