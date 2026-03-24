"""Tests for actions helpers and execute_actions."""

import io
import subprocess
from unittest.mock import MagicMock, patch

import pytest

import actions
import config
from reader import Message


def _sample_message() -> Message:
    return Message(
        rowid=1,
        chat_id=1,
        chat_identifier="+15550002222",
        sender="+15550002222",
        text="spam text",
        is_from_me=False,
        date=None,
        attributes=["SPAM"],
    )


def test_load_blocklist_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert actions.load_blocklist() == set()


def test_load_blocklist_reads_lines(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / actions.BLOCKLIST_FILE).write_text("+1a\n+1b\n\n", encoding="utf-8")
    assert actions.load_blocklist() == {"+1a", "+1b"}


def test_execute_actions_dry_run_no_subprocess(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "DRY_RUN", True)
    msg = _sample_message()

    with patch.object(actions.subprocess, "run") as run:
        results = actions.execute_actions(msg, ["send_stop", "log_only"])

    run.assert_not_called()
    assert results.get("send_stop") is True
    assert results.get("log_only") is True
    assert "send_stop" in msg.actions_taken
    assert "log_only" in msg.actions_taken


def test_block_sender_appends_blocklist(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "DRY_RUN", True)
    msg = _sample_message()

    assert actions.block_sender(msg) is True
    content = (tmp_path / actions.BLOCKLIST_FILE).read_text(encoding="utf-8")
    assert msg.chat_identifier in content
    assert "block" in msg.actions_taken


def test_unknown_action_reported_false():
    msg = _sample_message()
    results = actions.execute_actions(msg, ["not_a_real_action"])
    assert results["not_a_real_action"] is False


def test_run_applescript_success(monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", False)
    result = MagicMock(returncode=0, stderr=MagicMock(strip=lambda: ""), stdout="done")
    with patch.object(actions.subprocess, "run", return_value=result):
        ok, msg = actions._run_applescript("x")
    assert ok is True and msg == "done"


def test_run_applescript_subprocess_error(monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", False)
    result = MagicMock(returncode=1, stderr=MagicMock(strip=lambda: "osascript failed"), stdout="")
    with patch.object(actions.subprocess, "run", return_value=result):
        ok, msg = actions._run_applescript("tell app \"Messages\"")
    assert ok is False
    assert "failed" in msg


def test_run_applescript_timeout(monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", False)
    with patch.object(
        actions.subprocess,
        "run",
        side_effect=subprocess.TimeoutExpired(cmd="osascript", timeout=15),
    ):
        ok, msg = actions._run_applescript("x")
    assert ok is False and msg == "timeout"


def test_run_applescript_generic_exception(monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", False)
    with patch.object(actions.subprocess, "run", side_effect=OSError("nope")):
        ok, msg = actions._run_applescript("x")
    assert ok is False and "nope" in msg


def test_send_stop_skipped_when_messages_running_non_interactive(monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr(actions, "_is_messages_running", lambda: True)
    monkeypatch.setattr(actions.sys, "stdin", io.StringIO())
    called: list[str] = []

    def no_applescript(script: str):
        called.append(script)
        return True, ""

    monkeypatch.setattr(actions, "_run_applescript", no_applescript)

    msg = _sample_message()
    results = actions.execute_actions(msg, ["send_stop"])
    assert results["send_stop"] is False
    assert called == []


def test_delete_thread_skipped_when_messages_running_non_interactive(monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr(actions, "_is_messages_running", lambda: True)
    called: list[str] = []

    monkeypatch.setattr(
        actions, "_run_applescript", lambda s: called.append(s) or (True, "")
    )
    monkeypatch.setattr(actions.sys, "stdin", io.StringIO())

    msg = _sample_message()
    results = actions.execute_actions(msg, ["delete"])
    assert results["delete"] is False
    assert called == []


def test_messages_quit_guard_interactive_succeeds_after_quit(monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", False)
    states = [True, False]

    def running():
        return states.pop(0) if states else False

    monkeypatch.setattr(actions, "_is_messages_running", running)

    class _TtyStdin:
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(actions.sys, "stdin", _TtyStdin())
    monkeypatch.setattr("builtins.input", lambda _: "")

    assert actions._messages_quit_guard() is True


def test_send_stop_uses_fallback_script(monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr(actions, "_is_messages_running", lambda: False)
    calls: list[str] = []

    def fake(script: str):
        calls.append(script)
        if len(calls) == 1:
            return False, "first failed"
        return True, "ok"

    monkeypatch.setattr(actions, "_run_applescript", fake)
    assert actions.send_stop(_sample_message()) is True
    assert len(calls) == 2


def test_send_stop_both_fail(monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr(actions, "_is_messages_running", lambda: False)
    monkeypatch.setattr(actions, "_run_applescript", lambda s: (False, "no"))
    assert actions.send_stop(_sample_message()) is False


def test_delete_thread_failure_logs(monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr(actions, "_is_messages_running", lambda: False)
    monkeypatch.setattr(actions, "_run_applescript", lambda s: (False, "cannot delete"))
    assert actions.delete_thread(_sample_message()) is False


def test_delete_thread_success(monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr(actions, "_is_messages_running", lambda: False)
    monkeypatch.setattr(actions, "_run_applescript", lambda s: (True, ""))
    msg = _sample_message()
    assert actions.delete_thread(msg) is True
    assert "delete" in msg.actions_taken


def test_execution_action_order_archive_before_delete():
    assert actions._execution_action_order(["delete", "send_stop", "archive"]) == [
        "archive",
        "send_stop",
        "delete",
    ]


def test_execute_actions_runs_archive_before_delete(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "DRY_RUN", True)
    order: list[str] = []

    def arch(m):
        order.append("archive")
        m.actions_taken.append("archive")
        return True

    def dele(m):
        order.append("delete")
        m.actions_taken.append("delete")
        return True

    monkeypatch.setitem(actions.ACTION_MAP, "archive", arch)
    monkeypatch.setitem(actions.ACTION_MAP, "delete", dele)

    actions.execute_actions(_sample_message(), ["delete", "archive"])
    assert order == ["archive", "delete"]
