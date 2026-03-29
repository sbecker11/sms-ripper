"""Tests for rules.evaluate and policy-specific rule sets."""

import rules
from reader import Message


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


# --- policy=political (default): only POLITICAL is actioned for spam-like tags ---


def test_political_unsubscribe_confirmation_purges_not_archives():
    m = _msg(["LEGIT"])
    m.text = "You have unsubscribed from alerts."
    assert rules.evaluate(m) == ["purge"]


def test_political_delayed_unsubscribe_phrases_purge():
    for body in (
        "You have been unsubscribed.",
        "You've been unsubscribed from this service.",
        "Unsubscribe confirmed. You will not receive further messages.",
    ):
        m = _msg(["UNKNOWN"])
        m.text = body
        assert rules.evaluate(m) == ["purge"], repr(body)


def test_political_unsubscribe_phrase_in_both_subject_and_body_purges_once():
    m = _msg(["LEGIT"])
    m.subject = "You have been unsubscribed"
    m.text = "You have been unsubscribed. Reply HELP for help."
    assert rules.evaluate(m) == ["purge"]


def test_political_unsubscribe_text_prevents_archive_even_if_political_tag():
    m = _msg(["POLITICAL"])
    m.text = "You have unsubscribed."
    assert rules.evaluate(m) == ["purge"]


def test_political_spam_alone_log_only():
    assert rules.evaluate(_msg(["SPAM", "UNKNOWN"])) == ["log_only"]


def test_political_stop_alone_log_only():
    assert rules.evaluate(_msg(["STOP"])) == ["log_only"]


def test_political_scam_alone_log_only():
    assert rules.evaluate(_msg(["SCAM"])) == ["log_only"]


def test_political_spam_and_scam_log_only():
    assert rules.evaluate(_msg(["SPAM", "SCAM"])) == ["log_only"]


def test_political_political_archives_only_when_not_personal():
    assert rules.evaluate(_msg(["POLITICAL"])) == ["archive"]
    assert rules.evaluate(_msg(["POLITICAL", "PERSONAL"])) == []


def test_political_political_with_spam_uses_political_rule():
    assert rules.evaluate(_msg(["POLITICAL", "SPAM"])) == ["archive"]


def test_political_promo_log_only():
    assert rules.evaluate(_msg(["PROMO"])) == ["log_only"]


def test_political_promo_with_spam_log_only():
    assert rules.evaluate(_msg(["PROMO", "SPAM"])) == ["log_only"]


def test_political_legit_and_personal_no_actions():
    assert rules.evaluate(_msg(["LEGIT"])) == []
    assert rules.evaluate(_msg(["PERSONAL"])) == []


def test_political_unknown_defaults_to_log_only():
    assert rules.evaluate(_msg(["UNKNOWN"])) == ["log_only"]


def test_political_evaluate_detailed_returns_rule_names():
    actions, names = rules.evaluate_detailed(_msg(["POLITICAL"]))
    assert "archive" in actions
    assert names == ["political"]


def test_political_evaluate_detailed_no_match():
    actions, names = rules.evaluate_detailed(_msg(["UNKNOWN"]))
    assert actions == ["log_only"]
    assert names == []


# --- policy=spam: second pass ---


def test_spam_policy_spam_stop_block_delete():
    out = rules.evaluate(_msg(["SPAM"]), policy="spam")
    assert "send_stop" in out
    assert "block" in out
    assert "delete" in out


def test_spam_policy_stop_matches_spam_stop_rule():
    out = rules.evaluate(_msg(["STOP"]), policy="spam")
    assert "send_stop" in out and "delete" in out


def test_spam_policy_scam_block_delete_no_stop():
    out = rules.evaluate(_msg(["SCAM"]), policy="spam")
    assert out == ["block", "delete"]


def test_spam_policy_political_only_log_only():
    assert rules.evaluate(_msg(["POLITICAL"]), policy="spam") == ["log_only"]


def test_spam_policy_political_with_spam_uses_spam_rule_not_political():
    out = rules.evaluate(_msg(["POLITICAL", "SPAM"]), policy="spam")
    assert "send_stop" in out
    assert "delete" in out
    assert "archive" not in out
