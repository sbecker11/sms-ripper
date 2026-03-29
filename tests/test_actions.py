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


def test_mark_inbound_read_sets_is_read(monkeypatch, tmp_path):
    import sqlite3

    db = tmp_path / "chat.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE message (is_read INTEGER DEFAULT 0, is_from_me INTEGER DEFAULT 0, "
        "date INTEGER, date_read INTEGER DEFAULT 0)"
    )
    conn.execute(
        "INSERT INTO message (is_read, is_from_me, date, date_read) VALUES (0, 0, 12345, 0)"
    )
    rid = conn.execute("SELECT rowid FROM message").fetchone()[0]
    conn.commit()
    conn.close()

    monkeypatch.setattr(config, "CHAT_DB_PATH", str(db))
    monkeypatch.setattr(config, "DRY_RUN", False)
    msg = Message(
        rowid=rid,
        chat_id=1,
        chat_identifier="+1",
        sender="+1",
        text="x",
        is_from_me=False,
        date=None,
    )
    assert actions.mark_inbound_read(msg) is True
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT is_read, date_read FROM message WHERE rowid=?", (rid,)
        ).fetchone()
        assert row[0] == 1
        assert row[1] == 12345
    finally:
        conn.close()


def test_mark_inbound_read_skips_outbound(monkeypatch, tmp_path):
    import sqlite3

    db = tmp_path / "chat.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE message (is_read INTEGER DEFAULT 0, is_from_me INTEGER DEFAULT 0)"
    )
    conn.execute("INSERT INTO message VALUES (0, 1)")
    rid = conn.execute("SELECT rowid FROM message").fetchone()[0]
    conn.commit()
    conn.close()
    monkeypatch.setattr(config, "CHAT_DB_PATH", str(db))
    monkeypatch.setattr(config, "DRY_RUN", False)
    msg = Message(
        rowid=rid,
        chat_id=1,
        chat_identifier="+1",
        sender="+1",
        text="x",
        is_from_me=True,
        date=None,
    )
    assert actions.mark_inbound_read(msg) is True
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT is_read FROM message WHERE rowid=?", (rid,)).fetchone()[0] == 0
    finally:
        conn.close()


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


def test_block_sender_dry_run_does_not_append_blocklist(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "DRY_RUN", True)
    msg = _sample_message()

    assert actions.block_sender(msg) is True
    assert not (tmp_path / actions.BLOCKLIST_FILE).exists()
    assert "block" in msg.actions_taken


def test_block_sender_appends_blocklist_when_live(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "DRY_RUN", False)
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


def test_send_stop_runs_without_quit_guard_when_only_send_stop(monkeypatch):
    """Archive is what needs Messages quit; send_stop uses AppleScript and may run while Messages is up."""
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr(actions, "_is_messages_running", lambda: True)
    monkeypatch.setattr(actions.sys, "stdin", io.StringIO())
    called: list[str] = []

    def capture(script: str):
        called.append(script)
        return True, ""

    monkeypatch.setattr(actions, "_run_applescript", capture)

    msg = _sample_message()
    results = actions.execute_actions(msg, ["send_stop"])
    assert results["send_stop"] is True
    assert len(called) >= 1


def test_archive_skipped_when_messages_running_non_interactive(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr(actions, "_is_messages_running", lambda: True)
    monkeypatch.setattr(actions.sys, "stdin", io.StringIO())
    ran: list[str] = []

    monkeypatch.setitem(
        actions.ACTION_MAP,
        "archive",
        lambda m: ran.append("archive") or True,
    )
    msg = _sample_message()
    results = actions.execute_actions(msg, ["archive"])
    assert results["archive"] is False
    assert ran == []


def test_delete_thread_runs_without_sqlite_quit_guard(monkeypatch):
    """delete uses AppleScript, not direct sqlite; no Messages-quit prompt for delete alone."""
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr(actions, "_is_messages_running", lambda: True)
    called: list[str] = []

    monkeypatch.setattr(
        actions, "_run_applescript", lambda s: called.append(s) or (True, "")
    )
    monkeypatch.setattr(actions.sys, "stdin", io.StringIO())

    msg = _sample_message()
    results = actions.execute_actions(msg, ["delete"])
    assert results["delete"] is True
    assert len(called) >= 1


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
    """SMS attempt fails, next channel (iMessage) succeeds."""
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
    calls = {"n": 0}

    def fail(_script: str):
        calls["n"] += 1
        return False, "no"

    monkeypatch.setattr(actions, "_run_applescript", fail)
    assert actions.send_stop(_sample_message()) is False
    assert calls["n"] == 3


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


def test_execute_actions_batch_sqlite_ok_true_skips_guard(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "DRY_RUN", False)
    calls = {"n": 0}

    def guard() -> bool:
        calls["n"] += 1
        return True

    monkeypatch.setattr(actions, "_messages_quit_guard", guard)
    monkeypatch.setattr(actions, "_is_messages_running", lambda: True)
    arch_ran: list[int] = []
    monkeypatch.setitem(
        actions.ACTION_MAP,
        "archive",
        lambda m: arch_ran.append(1) or True,
    )
    msg = _sample_message()
    results = actions.execute_actions(msg, ["archive"], batch_sqlite_ok=True)
    assert calls["n"] == 0
    assert results["archive"] is True
    assert arch_ran == [1]


def test_action_list_needs_sqlite_archive():
    assert actions.action_list_needs_sqlite_archive(["send_stop", "archive"]) is True
    assert actions.action_list_needs_sqlite_archive(["purge"]) is True
    assert actions.action_list_needs_sqlite_archive(["send_stop", "delete"]) is False


def test_phase1_sqlite_complete():
    assert actions.phase1_sqlite_complete(["purge", "send_stop"], {"purge": True}) is True
    assert actions.phase1_sqlite_complete(["purge"], {"purge": False}) is False
    assert actions.phase1_sqlite_complete(["archive"], {"archive": True}) is True


def test_action_list_needs_messages_activate():
    assert actions.action_list_needs_messages_activate(["send_stop"]) is True
    assert actions.action_list_needs_messages_activate(["delete"]) is True
    assert actions.action_list_needs_messages_activate(["block", "log_only"]) is False


def test_activate_messages_dry_run_no_subprocess(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "DRY_RUN", True)
    with patch.object(actions.subprocess, "run") as run:
        assert actions.activate_messages() is True
    run.assert_not_called()


def test_execution_action_order_archive_before_delete():
    assert actions._execution_action_order(["delete", "send_stop", "archive"]) == [
        "archive",
        "send_stop",
        "delete",
    ]


def test_execution_action_order_purge_with_archive():
    assert actions._execution_action_order(["delete", "purge", "archive"]) == [
        "purge",
        "archive",
        "delete",
    ]


def test_execute_actions_archive_only_skips_ui(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "DRY_RUN", True)
    ran: list[str] = []

    monkeypatch.setitem(
        actions.ACTION_MAP,
        "archive",
        lambda m: ran.append("archive") or True,
    )
    monkeypatch.setitem(
        actions.ACTION_MAP,
        "send_stop",
        lambda m: ran.append("send_stop") or True,
    )
    msg = _sample_message()
    actions.execute_actions(msg, ["archive", "send_stop"], phases="archive_only")
    assert ran == ["archive"]


def test_execute_actions_ui_only_skips_archive(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "DRY_RUN", True)
    ran: list[str] = []

    monkeypatch.setitem(
        actions.ACTION_MAP,
        "archive",
        lambda m: ran.append("archive") or True,
    )
    monkeypatch.setitem(
        actions.ACTION_MAP,
        "send_stop",
        lambda m: ran.append("send_stop") or True,
    )
    msg = _sample_message()
    actions.execute_actions(msg, ["archive", "send_stop"], phases="ui_only")
    assert ran == ["send_stop"]


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
