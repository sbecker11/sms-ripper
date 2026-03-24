# rules.py
"""
Rules engine: maps message attributes → list of actions to take.

Actions:
  send_stop    — reply with STOP text
  block        — block the sender in Messages
  delete       — delete the entire chat thread
  log_only     — just log it, no action
"""

from dataclasses import dataclass
from typing import Callable
from reader import Message


@dataclass
class Rule:
    name: str
    description: str
    condition: Callable[[list[str]], bool]
    actions: list[str]


# --- Define your rules here ---

RULES: list[Rule] = [

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
        name="political",
        description="Political messaging — delete silently",
        condition=lambda attrs: "POLITICAL" in attrs and "PERSONAL" not in attrs,
        actions=["delete"],
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


def evaluate_detailed(message: Message) -> tuple[list[str], list[str]]:
    """
    Returns (merged_actions, matched_rule_names) in rule order.
    If no rule matches, actions become ["log_only"] and matched names stay empty.
    """
    all_actions: list[str] = []
    matched_rules: list[str] = []

    for rule in RULES:
        if rule.condition(message.attributes):
            matched_rules.append(rule.name)
            for action in rule.actions:
                if action not in all_actions:
                    all_actions.append(action)

    if not all_actions and not matched_rules:
        all_actions = ["log_only"]

    return all_actions, matched_rules


def evaluate(message: Message) -> list[str]:
    """
    Returns the merged list of actions for all matching rules, deduplicated
    in rule order. If no rule matches, returns ["log_only"].
    """
    actions, _ = evaluate_detailed(message)
    return actions
