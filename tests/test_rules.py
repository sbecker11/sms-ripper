"""Tests for rules.evaluate."""

from reader import Message
import rules


def _msg(attrs: list[str]) -> Message:
    return Message(
        rowid=1,
        chat_id=1,
        chat_identifier="+15550001111",
        sender="+15550001111",
        text="x",
        is_from_me=False,
        date=None,
        attributes=attrs,
    )


def test_spam_triggers_stop_block_delete():
    out = rules.evaluate(_msg(["SPAM", "UNKNOWN"]))
    assert "send_stop" in out
    assert "block" in out
    assert "delete" in out


def test_stop_alone_triggers_spam_stop_rule():
    out = rules.evaluate(_msg(["STOP"]))
    assert out == ["send_stop", "block", "delete"]


def test_scam_blocks_and_deletes_without_stop_first():
    out = rules.evaluate(_msg(["SCAM"]))
    assert out == ["block", "delete"]
    assert "send_stop" not in out


def test_spam_and_scam_merges_actions():
    out = rules.evaluate(_msg(["SPAM", "SCAM"]))
    assert "send_stop" in out
    assert "block" in out
    assert "delete" in out


def test_political_delete_only_when_not_personal():
    assert "delete" in rules.evaluate(_msg(["POLITICAL"]))
    assert rules.evaluate(_msg(["POLITICAL", "PERSONAL"])) == []


def test_promo_log_only():
    assert rules.evaluate(_msg(["PROMO"])) == ["log_only"]


def test_promo_with_spam_uses_spam_rule_not_promo_only():
    out = rules.evaluate(_msg(["PROMO", "SPAM"]))
    assert "send_stop" in out
    assert "log_only" not in out


def test_legit_and_personal_no_actions():
    assert rules.evaluate(_msg(["LEGIT"])) == []
    assert rules.evaluate(_msg(["PERSONAL"])) == []


def test_unknown_defaults_to_log_only():
    assert rules.evaluate(_msg(["UNKNOWN"])) == ["log_only"]


def test_evaluate_detailed_returns_rule_names():
    actions, names = rules.evaluate_detailed(_msg(["SPAM", "SCAM"]))
    assert "send_stop" in actions
    assert names == ["spam_stop", "scam"]


def test_evaluate_detailed_no_match():
    actions, names = rules.evaluate_detailed(_msg(["UNKNOWN"]))
    assert actions == ["log_only"]
    assert names == []
