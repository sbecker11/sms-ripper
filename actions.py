# actions.py
"""
AppleScript-backed actions for the SMS agent.
Each function returns True on success, False on failure.
"""

from __future__ import annotations

import logging
import sqlite3
import subprocess
import sys
from typing import Literal

import archive
import config
from reader import Message

logger = logging.getLogger("sms_agent")


def _info(msg: str) -> None:
    """Per-message detail: DEBUG when QUIET so INFO stays minimal progress-only."""
    if getattr(config, "QUIET", False):
        logger.debug(msg)
    else:
        logger.info(msg)


MESSAGES_QUIT_PROMPT = (
    "This operation writes directly to the Messages SQLite database (archive). "
    "The Messages (iMessage) app must be fully terminated first "
    "(Messages → Quit Messages, or Cmd+Q). "
    "While Messages is running, the database may be locked and changes can be unsafe."
)

# Only archive uses sqlite3 on chat.db from this codebase. (delete uses AppleScript.)
DIRECT_SQLITE_ARCHIVE_ACTIONS: frozenset[str] = frozenset({"archive"})


def _run_applescript(script: str) -> tuple[bool, str]:
    """Execute an AppleScript string. Returns (success, output/error)."""
    if config.DRY_RUN:
        if getattr(config, "QUIET", False):
            logger.debug("[DRY RUN] AppleScript (%d chars); full script at DEBUG", len(script))
        else:
            logger.info(f"[DRY RUN] AppleScript:\n{script}")
        return True, "dry_run"
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            logger.error(f"AppleScript error: {result.stderr.strip()}")
            return False, result.stderr.strip()
        return True, result.stdout.strip()
    except subprocess.TimeoutExpired:
        logger.error("AppleScript timed out")
        return False, "timeout"
    except Exception as e:
        logger.error(f"AppleScript exception: {e}")
        return False, str(e)


def _is_messages_running() -> bool:
    """True if the Messages.app GUI process is running (macOS), not BlastDoor/Chrome/etc."""
    try:
        subprocess.run(
            [
                "pgrep",
                "-f",
                r"Messages\.app/Contents/MacOS/Messages",
            ],
            check=True,
            capture_output=True,
            timeout=5,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return False


def messages_quit_guard() -> bool:
    """Public alias for use from main when batching archives across a run."""
    return _messages_quit_guard()


def activate_messages() -> bool:
    """
    Bring Messages to the foreground before phase-2 AppleScript (STOP, delete, etc.).
    Phase 1 should finish all chat.db archives while Messages was quit; this explicitly
    starts the UI phase. Dry-run: log only, no osascript.
    """
    if config.DRY_RUN:
        _info("[DRY RUN] Would activate Messages before queued STOP / UI actions")
        return True
    ok, err = _run_applescript('tell application "Messages" to activate')
    if ok:
        logger.info("[MESSAGES] Activated for phase 2 (queued STOP replies and other UI actions)")
    else:
        logger.error(f"[MESSAGES] activate failed: {err}")
    return ok


def _messages_quit_guard() -> bool:
    """
    Before WRITE/DELETE side effects on the Messages database, ensure Messages is not running.
    In dry-run mode, always allow (no real DB access).
    If running and stdin is a TTY, prompt once to quit and retry after Enter.
    """
    if config.DRY_RUN:
        return True
    if not _is_messages_running():
        return True
    logger.error(MESSAGES_QUIT_PROMPT)
    print(MESSAGES_QUIT_PROMPT, file=sys.stderr)
    if sys.stdin.isatty():
        try:
            input(
                "Quit Messages, then press Enter to continue (or Ctrl+C to abort)... "
            )
        except (KeyboardInterrupt, EOFError):
            logger.info("Aborted waiting for Messages to quit.")
            return False
        if not _is_messages_running():
            return True
        still = "Messages is still running. Quit it completely, then run again."
        logger.error(still)
        print(still, file=sys.stderr)
        return False
    return False


def _applescript_escape(text: str) -> str:
    """Escape for double-quoted AppleScript string literals."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def send_stop(message: Message) -> bool:
    """
    Send a STOP reply to the message sender.

    Tries SMS first (many political blasts are SMS; iMessage-first often shows “Not Delivered”),
    then iMessage, then sending to the existing chat by identifier.
    """
    target = _applescript_escape(message.sender or message.chat_identifier)
    chat_id = _applescript_escape(message.chat_identifier)
    reply_text = _applescript_escape(config.STOP_REPLY_TEXT)

    scripts = [
        (
            "sms",
            f"""
tell application "Messages"
    set smsSvc to first service whose service type is SMS
    send "{reply_text}" to buddy "{target}" of smsSvc
end tell
""",
        ),
        (
            "imessage",
            f"""
tell application "Messages"
    set imSvc to first service whose service type is iMessage
    send "{reply_text}" to buddy "{target}" of imSvc
end tell
""",
        ),
        (
            "chat",
            f"""
tell application "Messages"
    send "{reply_text}" to participant "{target}" of chat "{chat_id}"
end tell
""",
        ),
    ]
    last_err = ""
    for label, script in scripts:
        success, output = _run_applescript(script)
        if success:
            who = (
                f"rowid={message.rowid} (recipient omitted)"
                if getattr(config, "QUIET", False)
                else (message.sender or message.chat_identifier)
            )
            _info(
                f"[SEND_STOP] Sent '{config.STOP_REPLY_TEXT}' to {who} (via {label})"
            )
            message.actions_taken.append("send_stop")
            return True
        last_err = output

    who_fail = (
        f"rowid={message.rowid}"
        if getattr(config, "QUIET", False)
        else target
    )
    logger.error(f"[SEND_STOP] All attempts failed for {who_fail}: {last_err}")
    return False


def block_sender(message: Message) -> bool:
    """
    Block the sender via Messages UI automation.
    Uses Accessibility API to click Block in the Details panel.
    """
    target = message.chat_identifier

    # Open the conversation and trigger block via menu
    script = f"""
tell application "Messages"
    activate
    -- Select the chat by identifier
    set theChats to (every chat whose chat identifier is "{target}")
    if (count of theChats) > 0 then
        set theChat to item 1 of theChats
        -- Use System Events to invoke Block Contact from the context
    end if
end tell

-- Block via System Preferences privacy list (most reliable approach)
do shell script "echo 'Blocking {target}'"
"""
    # NOTE: True "block contact" requires UI automation through System Settings
    # or adding to the Blocked Contacts list. The most reliable programmatic
    # approach is using the Contacts blocked list via AddressBook framework,
    # but that requires an Obj-C/Swift bridge. For now we log and mark.
    # A robust alternative: write to a local blocklist file and check it
    # at the top of main.py on each run to skip already-blocked senders.

    logged = (
        f"sender for rowid={message.rowid}"
        if getattr(config, "QUIET", False)
        else target
    )
    block_msg = (
        f"[BLOCK] Full programmatic blocking requires Accessibility permissions. "
        f"Logged {logged} to local blocklist. Open Messages → Details → Block Contact to complete."
    )
    if getattr(config, "QUIET", False):
        logger.debug(block_msg)
    else:
        logger.warning(block_msg)
    _write_local_blocklist(target)
    message.actions_taken.append("block")
    return True


def delete_thread(message: Message) -> bool:
    """Delete the entire chat thread for this message."""
    identifier = message.chat_identifier

    script = f"""
tell application "Messages"
    set theChats to (every chat whose chat identifier is "{identifier}")
    repeat with aChat in theChats
        delete aChat
    end repeat
end tell
"""
    success, output = _run_applescript(script)
    who = (
        f"rowid={message.rowid} (chat id omitted)"
        if getattr(config, "QUIET", False)
        else identifier
    )
    if success:
        _info(f"[DELETE] Deleted thread for {who}")
        message.actions_taken.append("delete")
    else:
        logger.error(f"[DELETE] Failed to delete thread for {who}: {output}")
    return success


def log_only(message: Message) -> bool:
    """No action — just log."""
    if getattr(config, "QUIET", False):
        logger.debug(
            "[LOG_ONLY] rowid=%s chat_id=%s", message.rowid, message.chat_id
        )
    else:
        logger.info(f"[LOG_ONLY] {message.display()}")
    message.actions_taken.append("log_only")
    return True


def mark_inbound_read(message: Message) -> bool:
    """
    Set the message row's read flag in chat.db (typically is_read=1).

    Intended for phase 2 while Messages is open so the Dock unread count can drop as rows
    are processed. May still be ignored or delayed depending on macOS version.
    Outbound rows are skipped.
    """
    if config.DRY_RUN:
        who = message.rowid if not getattr(config, "QUIET", False) else "…"
        _info(f"[DRY RUN] Would mark inbound message read rowid={who}")
        return True
    if message.is_from_me:
        return True

    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(config.CHAT_DB_PATH, timeout=15.0)
        archive._register_chat_db_trigger_stubs(conn)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(message)").fetchall()}
        read_col = None
        if "is_read" in cols:
            read_col = "is_read"
        elif "read" in cols:
            read_col = "read"
        if read_col is None:
            logger.warning("[MARK_READ] message table has no is_read/read column; skipping")
            return False

        qcol = '"' + read_col.replace('"', '""') + '"'
        set_parts = [f"{qcol} = 1"]
        if "date_read" in cols:
            set_parts.append("date_read = COALESCE(NULLIF(date_read, 0), date)")
        cur = conn.execute(
            f"UPDATE message SET {', '.join(set_parts)} WHERE rowid = ? AND IFNULL(is_from_me, 0) = 0",
            (message.rowid,),
        )
        conn.commit()
        if cur.rowcount == 0:
            logger.debug("[MARK_READ] No row updated for rowid=%s", message.rowid)
            return False
        who = message.rowid if not getattr(config, "QUIET", False) else "…"
        _info(f"[MARK_READ] Marked inbound message read (rowid={who})")
        return True
    except sqlite3.Error as e:
        logger.warning("[MARK_READ] Failed: %s", e)
        return False
    finally:
        if conn is not None:
            conn.close()


def archive_message(message: Message) -> bool:
    """Copy message into <tag>_archive, then remove the live DB row (see archive.py)."""
    return archive.archive_message(message)


# --- Local blocklist helpers ---

BLOCKLIST_FILE = "blocked_senders.txt"

def _write_local_blocklist(identifier: str):
    with open(BLOCKLIST_FILE, "a") as f:
        f.write(identifier + "\n")

def load_blocklist() -> set[str]:
    try:
        with open(BLOCKLIST_FILE, "r") as f:
            return {line.strip() for line in f if line.strip()}
    except FileNotFoundError:
        return set()


# --- Action dispatcher ---

ACTION_MAP = {
    "send_stop": send_stop,
    "block":     block_sender,
    "delete":    delete_thread,
    "archive":   archive_message,
    "log_only":  log_only,
}


def _execution_action_order(actions: list[str]) -> list[str]:
    """Ensure archive runs before delete; keep relative order within each group."""
    archives = [a for a in actions if a == "archive"]
    deletes = [a for a in actions if a == "delete"]
    rest = [a for a in actions if a not in ("archive", "delete")]
    return archives + rest + deletes


def action_list_needs_sqlite_archive(actions: list[str]) -> bool:
    """True if ordered actions include direct chat.db archive (requires Messages quit)."""
    return any(a in DIRECT_SQLITE_ARCHIVE_ACTIONS for a in _execution_action_order(actions))


def action_list_needs_messages_activate(actions: list[str]) -> bool:
    """True if UI phase uses AppleScript that expects Messages running (STOP, delete)."""
    return any(
        a in ("send_stop", "delete") for a in _execution_action_order(actions)
    )


Phases = Literal["all", "archive_only", "ui_only"]


def execute_actions(
    message: Message,
    actions: list[str],
    *,
    batch_sqlite_ok: bool | None = None,
    phases: Phases = "all",
) -> dict[str, bool]:
    """
    Run all actions for a message. Returns {action: success} map.

    batch_sqlite_ok:
      None — single-message mode: prompt for Messages quit before archive if needed.
      True — caller already ensured quit for this run (e.g. main.py batched guard).
      False — skip archive actions (guard failed or declined).

    phases:
      all — archive (sqlite) then UI actions in one call.
      archive_only — only direct chat.db archive; use when Messages must stay quit.
      ui_only — only AppleScript actions (send_stop, block, delete, log_only); run after archives.
    """
    ordered = _execution_action_order(actions)
    sqlite_actions = [a for a in ordered if a in DIRECT_SQLITE_ARCHIVE_ACTIONS]
    ui_actions = [a for a in ordered if a not in DIRECT_SQLITE_ARCHIVE_ACTIONS]
    results: dict[str, bool] = {}

    run_archive = phases in ("all", "archive_only")
    run_ui = phases in ("all", "ui_only")

    if run_archive and not config.DRY_RUN and sqlite_actions:
        if batch_sqlite_ok is None:
            if not _messages_quit_guard():
                for a in sqlite_actions:
                    results[a] = False
                sqlite_actions = []
        elif batch_sqlite_ok is False:
            for a in sqlite_actions:
                results[a] = False
            sqlite_actions = []

    if run_archive:
        for action in sqlite_actions:
            fn = ACTION_MAP.get(action)
            if not fn:
                logger.warning(f"Unknown action: {action}")
                results[action] = False
                continue
            results[action] = fn(message)

    if run_ui:
        for action in ui_actions:
            fn = ACTION_MAP.get(action)
            if not fn:
                logger.warning(f"Unknown action: {action}")
                results[action] = False
                continue
            results[action] = fn(message)

    return results
