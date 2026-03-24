#!/usr/bin/env bash
# Quit the Messages *app* (main GUI process) if it is running (macOS).
#
# ps/grep "message" often matches MessagesBlastDoorService, MessagesActionExtension,
# or Chrome's --message-loop-type-ui — those are NOT Messages.app and are ignored here.
#
# Main process path contains: Messages.app/Contents/MacOS/Messages
#
# Automation: System Settings → Privacy & Security → Automation → allow this terminal
# (or Cursor) to control "Messages".
set -euo pipefail

# True only for the real Messages.app executable (not BlastDoor, extensions, Chrome, etc.).
main_messages_running() {
	pgrep -f 'Messages\.app/Contents/MacOS/Messages' >/dev/null 2>&1
}

if ! main_messages_running; then
	echo "Messages.app (main GUI) is not running." >&2
	echo "Tip: lines in ps like MessagesBlastDoorService, MessagesActionExtension, or Chrome" >&2
	echo "     helpers with --message-loop are not the Messages window — no quit needed." >&2
	exit 0
fi

if ! osascript <<'APPLESCRIPT'
tell application "System Events"
	set msgsRunning to (exists process "Messages")
end tell
if msgsRunning then
	tell application "Messages" to quit saving no
end if
APPLESCRIPT
then
	echo "AppleScript failed or was denied. Often: System Settings → Privacy & Security → Automation" >&2
	echo "→ enable your terminal (or Cursor) for \"Messages\"." >&2
	exit 1
fi

sleep 1

if main_messages_running; then
	echo "Messages still running after AppleScript; sending SIGTERM." >&2
	killall -TERM Messages 2>/dev/null || true
	sleep 1
fi

if main_messages_running; then
	echo "Messages.app is still running. Close it manually or check Activity Monitor." >&2
	exit 1
fi

echo "Messages.app has quit."
