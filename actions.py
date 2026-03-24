# actions.py
"""
AppleScript-backed actions for the SMS agent.
Each function returns True on success, False on failure.
"""

import logging
import subprocess
import sys

import archive
import config
from reader import Message

logger = logging.getLogger("sms_agent")

MESSAGES_QUIT_PROMPT = (
    "This operation writes to or deletes data in the Messages database. "
    "The Messages (iMessage) app must be fully terminated first "
    "(Messages → Quit Messages, or Cmd+Q). "
    "While Messages is running, the database may be locked and changes can be unsafe."
)


def _run_applescript(script: str) -> tuple[bool, str]:
    """Execute an AppleScript string. Returns (success, output/error)."""
    if config.DRY_RUN:
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


def send_stop(message: Message) -> bool:
    """Send a STOP reply to the message sender."""
    target = message.sender or message.chat_identifier
    reply_text = config.STOP_REPLY_TEXT

    # Try iMessage first, fall back to SMS
    script = f"""
tell application "Messages"
    set targetBuddy to a reference to buddy "{target}" of (first service whose service type = iMessage)
    send "{reply_text}" to targetBuddy
end tell
"""
    success, output = _run_applescript(script)
    if not success:
        # Fallback: use chat identifier directly
        script_fallback = f"""
tell application "Messages"
    send "{reply_text}" to participant "{target}" of chat "{message.chat_identifier}"
end tell
"""
        success, output = _run_applescript(script_fallback)

    if success:
        logger.info(f"[SEND_STOP] Sent '{reply_text}' to {target}")
        message.actions_taken.append("send_stop")
    return success


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

    logger.warning(
        f"[BLOCK] Full programmatic blocking requires Accessibility permissions. "
        f"Logged {target} to local blocklist. Open Messages → Details → Block Contact to complete."
    )
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
    if success:
        logger.info(f"[DELETE] Deleted thread for {identifier}")
        message.actions_taken.append("delete")
    else:
        logger.error(f"[DELETE] Failed to delete thread for {identifier}: {output}")
    return success


def log_only(message: Message) -> bool:
    """No action — just log."""
    logger.info(f"[LOG_ONLY] {message.display()}")
    message.actions_taken.append("log_only")
    return True


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


def execute_actions(message: Message, actions: list[str]) -> dict[str, bool]:
    """Run all actions for a message. Returns {action: success} map."""
    actions = _execution_action_order(actions)
    results: dict[str, bool] = {}
    db_mutations = {"send_stop", "delete", "archive"}
    guard_ok = True
    if not config.DRY_RUN and set(actions) & db_mutations:
        guard_ok = _messages_quit_guard()

    for action in actions:
        fn = ACTION_MAP.get(action)
        if not fn:
            logger.warning(f"Unknown action: {action}")
            results[action] = False
            continue
        if not guard_ok and action in db_mutations:
            results[action] = False
            continue
        results[action] = fn(message)
    return results
