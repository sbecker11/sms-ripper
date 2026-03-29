#!/usr/bin/env bash
# When chat.db shows 0 unread (poe badge-diagnose) but Dock / Messages UI still show unread:
# quit Messages and restart UI services that cache badge counts. Non-destructive.
#
# Usage: bash scripts/fix_messages_badge_stuck.sh
# Then reopen Messages and check the badge.

set -euo pipefail

echo "Quitting Messages…"
osascript -e 'tell application "Messages" to if it is running then quit' || true
sleep 2

echo "Restarting Notification Center (clears in-memory notification/badge state)…"
killall NotificationCenter 2>/dev/null || true
sleep 1

# User-notification daemon (name varies by macOS); ignore if not running.
for d in usernotificationsd usernoted; do
  if killall "$d" 2>/dev/null; then
    echo "Signaled $d to exit (will respawn)."
    sleep 1
  fi
done

echo "Restarting Dock (refreshes app icon badges)…"
killall Dock
sleep 2

echo "Done. Open Messages when ready; badge should match notification state."
echo "If Unread list is still wrong, that is usually iCloud — use iPhone Read All on Wi‑Fi."
