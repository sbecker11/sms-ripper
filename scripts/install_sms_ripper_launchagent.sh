#!/usr/bin/env bash
# Install or remove the per-user LaunchAgent that runs sms-ripper every N seconds (default 900 = 15 min).
#
# Usage:
#   scripts/install_sms_ripper_launchagent.sh install [interval_seconds]
#   scripts/install_sms_ripper_launchagent.sh uninstall
#   scripts/install_sms_ripper_launchagent.sh status
#
# Requires: repo venv at ./venv/bin/python, Full Disk Access for the parent process loading launchd.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.smsripper.periodic"
PLIST_NAME="${LABEL}.plist"
DEST="${HOME}/Library/LaunchAgents/${PLIST_NAME}"
VPY="${REPO_ROOT}/venv/bin/python"
CYCLE="${REPO_ROOT}/scripts/daemon_cycle.py"

usage() {
	echo "Usage: $0 install [interval_seconds] | uninstall | status" >&2
	echo "  install [900]  — write LaunchAgent and load it (default interval 900s = 15 min)" >&2
	echo "  uninstall      — unload and remove the plist" >&2
	echo "  status         — print whether the job is loaded" >&2
	exit 2
}

ensure_venv() {
	if [[ ! -x "$VPY" ]]; then
		echo "error: missing executable venv: $VPY" >&2
		echo "Create it from the repo root: python3 -m venv venv && ./venv/bin/pip install -r requirements.txt" >&2
		exit 1
	fi
	if [[ ! -f "$CYCLE" ]]; then
		echo "error: missing $CYCLE" >&2
		exit 1
	fi
}

write_plist() {
	local interval="$1"
	cat >"$DEST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>${LABEL}</string>
	<key>WorkingDirectory</key>
	<string>${REPO_ROOT}</string>
	<key>ProgramArguments</key>
	<array>
		<string>${VPY}</string>
		<string>${CYCLE}</string>
	</array>
	<key>RunAtLoad</key>
	<true/>
	<key>StartInterval</key>
	<integer>${interval}</integer>
	<key>StandardOutPath</key>
	<string>/dev/null</string>
	<key>StandardErrorPath</key>
	<string>/dev/null</string>
	<key>EnvironmentVariables</key>
	<dict>
		<key>PATH</key>
		<string>/usr/bin:/bin:/usr/sbin:/sbin</string>
	</dict>
</dict>
</plist>
PLIST
	echo "Wrote $DEST (interval=${interval}s)"
}

unload_agent() {
	if [[ -f "$DEST" ]]; then
		launchctl bootout "gui/$(id -u)" "$DEST" 2>/dev/null || launchctl unload "$DEST" 2>/dev/null || true
	fi
}

load_agent() {
	launchctl bootstrap "gui/$(id -u)" "$DEST" 2>/dev/null || launchctl load -w "$DEST"
	echo "Loaded ${LABEL}. Logs: ${REPO_ROOT}/logs/daemon.log"
}

cmd="${1:-}"
case "$cmd" in
install)
	interval="${2:-900}"
	if ! [[ "$interval" =~ ^[0-9]+$ ]] || [[ "$interval" -lt 60 ]]; then
		echo "error: interval must be an integer >= 60 seconds" >&2
		exit 1
	fi
	ensure_venv
	mkdir -p "${HOME}/Library/LaunchAgents"
	unload_agent
	write_plist "$interval"
	load_agent
	echo "Done. The job runs at login and every ${interval}s. Test once: ${VPY} ${CYCLE}"
	echo ""
	echo "Full Disk Access — add every path below (/bin/cp first). Guided setup: poe fda-assist"
	"${REPO_ROOT}/venv/bin/python" "${REPO_ROOT}/scripts/print_fda_python.py" || true
	;;
uninstall)
	unload_agent
	if [[ -f "$DEST" ]]; then
		rm -f "$DEST"
		echo "Removed $DEST"
	else
		echo "No plist at $DEST"
	fi
	;;
status)
	if launchctl print "gui/$(id -u)/${LABEL}" &>/dev/null; then
		echo "Loaded: gui/$(id -u)/${LABEL}"
	else
		echo "Not loaded. Plist: $DEST (install with: $0 install 900)"
	fi
	if [[ -f "${REPO_ROOT}/logs/daemon.log" ]]; then
		echo "Last log lines:"
		tail -n 8 "${REPO_ROOT}/logs/daemon.log"
	fi
	;;
*)
	usage
	;;
esac
