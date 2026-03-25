# rules.py
"""
Rules engine: maps message attributes → list of actions to take.

Policies (see evaluate_detailed policy=):
  political — archive / STOP / block for POLITICAL (non-personal) only; use first in a two-pass flow.
  spam      — send_stop / block / delete for SPAM or STOP; SCAM rule; no political rule (run second).

Actions:
  send_stop    — reply with STOP text
  block        — block the sender in Messages
  delete       — delete the entire chat thread
  archive      — copy message row into <tag>_archive in chat.db, then remove the live row
  log_only     — just log it, no action
"""

from dataclasses import dataclass
from typing import Callable, Literal

from reader import Message

Policy = Literal["political", "spam"]


@dataclass
class Rule:
    name: str
    description: str
    condition: Callable[[list[str]], bool]
    actions: list[str]


# --- political first: only POLITICAL (non-personal) is actioned ---

RULES_POLITICAL: list[Rule] = [
    Rule(
        name="political",
        description="Political messaging — archive, send STOP, add sender to blocklist",
        condition=lambda attrs: "POLITICAL" in attrs and "PERSONAL" not in attrs,
        actions=["archive", "send_stop", "block"],
    ),
    Rule(
        name="promo_only",
        description="Promotional but not spam — log only",
        condition=lambda attrs: "PROMO" in attrs and "SPAM" not in attrs,
        actions=["log_only"],
    ),
    Rule(
        name="legit",
        description="Legitimate message — no action",
        condition=lambda attrs: "LEGIT" in attrs,
        actions=[],
    ),
    Rule(
        name="personal",
        description="Personal message — no action",
        condition=lambda attrs: "PERSONAL" in attrs,
        actions=[],
    ),
]

# --- spam second: no political rule (POLITICAL rows should already be archived) ---

RULES_SPAM: list[Rule] = [
    Rule(
        name="spam_stop",
        description="Message is SPAM and/or STOP — reply STOP, block, delete",
        condition=lambda attrs: bool({"SPAM", "STOP"} & set(attrs)),
        actions=["send_stop", "block", "delete"],
    ),
    Rule(
        name="scam",
        description="Message is a SCAM — block and delete without replying",
        condition=lambda attrs: "SCAM" in attrs,
        actions=["block", "delete"],
    ),
    Rule(
        name="promo_only",
        description="Promotional but not spam — log only",
        condition=lambda attrs: "PROMO" in attrs and "SPAM" not in attrs,
        actions=["log_only"],
    ),
    Rule(
        name="legit",
        description="Legitimate message — no action",
        condition=lambda attrs: "LEGIT" in attrs,
        actions=[],
    ),
    Rule(
        name="personal",
        description="Personal message — no action",
        condition=lambda attrs: "PERSONAL" in attrs,
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
        if rule.condition(message.attributes):
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
