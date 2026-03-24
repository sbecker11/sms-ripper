"""Tests for main.process_once and main.main (CLI wiring)."""

import sys
from datetime import datetime
from unittest.mock import MagicMock

import pytest

import config
from reader import Message


def _make_message(**kwargs) -> Message:
    defaults: dict = {
        "rowid": 1,
        "chat_id": 1,
        "chat_identifier": "+15550003333",
        "sender": "+15550003333",
        "text": "hello",
        "is_from_me": False,
        "date": datetime(2025, 1, 1, 12, 0, 0),
    }
    defaults.update(kwargs)
    return Message(**defaults)


@pytest.fixture
def main_mod(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Fresh import of main with log file under tmp_path."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "LOG_FILE", str(tmp_path / "sms_agent.log"))
    monkeypatch.setattr(config, "DRY_RUN", False)
    sys.modules.pop("main", None)
    import main as m

    return m


def test_process_once_no_messages(main_mod, monkeypatch):
    monkeypatch.setattr(main_mod.reader, "get_recent_messages", lambda **k: [])
    main_mod.process_once(10, 60)


def test_process_once_file_not_found(main_mod, monkeypatch):
    monkeypatch.setattr(
        main_mod.reader,
        "get_recent_messages",
        lambda **k: (_ for _ in ()).throw(FileNotFoundError("no db")),
    )
    main_mod.process_once(10, 60)


def test_process_once_read_error(main_mod, monkeypatch):
    monkeypatch.setattr(
        main_mod.reader,
        "get_recent_messages",
        lambda **k: (_ for _ in ()).throw(RuntimeError("sqlite")),
    )
    main_mod.process_once(10, 60)


def test_process_once_skips_blocklisted(main_mod, monkeypatch):
    monkeypatch.setattr(main_mod.actions, "load_blocklist", lambda: {"+15550003333"})
    monkeypatch.setattr(
        main_mod.reader,
        "get_recent_messages",
        lambda **k: [_make_message()],
    )
    main_mod.process_once(10, 60)


def test_process_once_classify_error(main_mod, monkeypatch):
    monkeypatch.setattr(main_mod.actions, "load_blocklist", lambda: set())
    monkeypatch.setattr(
        main_mod.reader,
        "get_recent_messages",
        lambda **k: [_make_message()],
    )
    monkeypatch.setattr(
        main_mod.classifier,
        "classify_message",
        lambda t: (_ for _ in ()).throw(ValueError("bad key")),
    )
    main_mod.process_once(10, 60)


def test_process_once_log_only_branch(main_mod, monkeypatch):
    monkeypatch.setattr(main_mod.actions, "load_blocklist", lambda: set())
    monkeypatch.setattr(
        main_mod.reader,
        "get_recent_messages",
        lambda **k: [_make_message()],
    )
    monkeypatch.setattr(
        main_mod.classifier,
        "classify_message",
        lambda t: (["PROMO"], "promo"),
    )
    monkeypatch.setattr(main_mod.rules, "evaluate_detailed", lambda m: (["log_only"], ["promo_only"]))
    main_mod.process_once(10, 60)


def test_process_once_executes_actions_success(main_mod, monkeypatch):
    monkeypatch.setattr(main_mod.actions, "load_blocklist", lambda: set())
    monkeypatch.setattr(
        main_mod.reader,
        "get_recent_messages",
        lambda **k: [_make_message()],
    )
    monkeypatch.setattr(
        main_mod.classifier,
        "classify_message",
        lambda t: (["SPAM"], "spam"),
    )
    monkeypatch.setattr(
        main_mod.actions,
        "execute_actions",
        lambda m, a: {"send_stop": True},
    )
    main_mod.process_once(10, 60)


def test_process_once_execute_all_fail(main_mod, monkeypatch):
    monkeypatch.setattr(main_mod.actions, "load_blocklist", lambda: set())
    monkeypatch.setattr(
        main_mod.reader,
        "get_recent_messages",
        lambda **k: [_make_message()],
    )
    monkeypatch.setattr(
        main_mod.classifier,
        "classify_message",
        lambda t: (["SPAM"], ""),
    )
    monkeypatch.setattr(
        main_mod.actions,
        "execute_actions",
        lambda m, a: {"send_stop": False},
    )
    main_mod.process_once(10, 60)


def test_main_dry_run_and_process_once(main_mod, monkeypatch):
    monkeypatch.setattr(main_mod, "process_once", MagicMock())
    monkeypatch.setattr(sys, "argv", ["main.py", "--dry-run"])
    config.DRY_RUN = False
    main_mod.main()
    assert config.DRY_RUN is True
    main_mod.process_once.assert_called_once()


def test_main_loop_keyboard_interrupt(main_mod, monkeypatch):
    calls = {"n": 0}

    def proc(limit, lookback):
        calls["n"] += 1
        if calls["n"] == 1:
            raise KeyboardInterrupt

    monkeypatch.setattr(main_mod, "process_once", proc)
    monkeypatch.setattr(main_mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(sys, "argv", ["main.py", "--loop", "1"])
    main_mod.main()
    assert calls["n"] == 1


def test_main_loop_sleeps_between_iterations(main_mod, monkeypatch):
    sleeps: list[int] = []
    n = {"i": 0}

    def proc(limit, lookback):
        n["i"] += 1
        if n["i"] == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(main_mod, "process_once", proc)
    monkeypatch.setattr(main_mod.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(sys, "argv", ["main.py", "--loop", "42"])
    main_mod.main()
    assert sleeps == [42]
    assert n["i"] == 2
