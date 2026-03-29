#!/usr/bin/env python3
"""
One daemon cycle for launchd: backup chat.db, quit Messages, political pass, badge sync.

Logs everything to logs/daemon.log. On failure, shows a macOS alert with the log path.
Skips if a previous cycle still holds the lock (overlapping runs).
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_scripts_dir = Path(__file__).resolve().parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

import macos_fda_paths  # noqa: E402

_REPO = Path(__file__).resolve().parent.parent
_LOG = _REPO / "logs" / "daemon.log"
_LOCK_PARENT = Path.home() / "Library" / "Caches" / "com.smsripper.periodic"
_LOCK_PATH = _LOCK_PARENT / "cycle.lock"

_REMEDY = (
    "Remedies: (1) logs/daemon.log — full stderr is there. "
    "(2) Full Disk Access: poe daemon-fda-path — add Python.app AND bin/python3.11 (and sqlite3). "
    "(3) Automation → Messages for quit/Dock scripts. "
    "(4) Quit Messages if stuck; wait for next cycle. "
    "(5) .env needs ANTHROPIC_API_KEY."
)

_app = macos_fda_paths.framework_python_app_executable(_REPO)
_bin = macos_fda_paths.resolved_venv_python(_REPO)
_BACKUP_FAIL_HINT = (
    "Backup: add /bin/cp to Full Disk Access first, then Python.app + bin. "
    f"poe fda-assist | Python.app: {_app or 'n/a'} | bin: {_bin or 'n/a'}"
)


def _venv_python() -> Path:
    return _REPO / "venv" / "bin" / "python"


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log_line(msg: str, logf) -> None:
    line = f"{_ts()} {msg}\n"
    logf.write(line)
    logf.flush()


def _alert(title: str, detail: str) -> None:
    body = f"{title}\n\n{detail}\n\n{_REMEDY}\n\nLog: {_LOG}"
    py = _venv_python() if _venv_python().is_file() else Path(sys.executable)
    subprocess.run(
        [str(py), str(_REPO / "scripts" / "daemon_alert.py"), body],
        cwd=str(_REPO),
        check=False,
        env=os.environ.copy(),
    )


def _run_step(
    cmd: list[str],
    step: str,
    logf,
    env: dict[str, str],
) -> int:
    _log_line(f"START {step}: {' '.join(cmd)}", logf)
    r = subprocess.run(
        cmd,
        cwd=str(_REPO),
        env=env,
        stdout=logf,
        stderr=subprocess.STDOUT,
    )
    if r.returncode == 0:
        _log_line(f"OK {step}", logf)
    else:
        _log_line(f"FAIL {step} exit={r.returncode}", logf)
    return int(r.returncode)


def main() -> int:
    _LOG.parent.mkdir(parents=True, exist_ok=True)
    _LOCK_PARENT.mkdir(parents=True, exist_ok=True)

    vpy = _venv_python()
    if not vpy.is_file():
        with open(_LOG, "a", encoding="utf-8") as logf:
            _log_line(
                "ERROR: venv/bin/python missing. Create the venv in the repo root, then reinstall the LaunchAgent.",
                logf,
            )
        _alert(
            "SMS Ripper daemon misconfigured",
            "venv/bin/python not found at repo root.",
        )
        return 1

    lock_f = open(_LOCK_PATH, "w", encoding="utf-8")
    try:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_f.close()
        with open(_LOG, "a", encoding="utf-8") as logf:
            _log_line("SKIP: previous cycle still running (lock held)", logf)
        return 0

    env = os.environ.copy()
    env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin:" + env.get("PATH", "")

    exit_code = 0
    try:
        with open(_LOG, "a", encoding="utf-8") as logf:
            _log_line(f"======== cycle start pid={os.getpid()} ========", logf)

            steps: list[tuple[str, list[str]]] = [
                ("backup", [str(vpy), str(_REPO / "scripts" / "backup_chat_db.py")]),
                ("quit_messages", ["/bin/bash", str(_REPO / "scripts" / "quit_messages.sh")]),
                (
                    "main_political",
                    [
                        str(vpy),
                        str(_REPO / "main.py"),
                        "--quiet",
                        "--lookback",
                        "10080",
                        "--limit",
                        "500",
                    ],
                ),
                (
                    "bulk_mark_read",
                    [
                        str(vpy),
                        str(_REPO / "scripts" / "bulk_mark_read.py"),
                        "--keep-unread",
                        "0",
                        "--fix-joined-outbound-read",
                    ],
                ),
                (
                    "fix_badge_shell",
                    ["/bin/bash", str(_REPO / "scripts" / "fix_messages_badge_stuck.sh")],
                ),
            ]

            for step, cmd in steps:
                code = _run_step(cmd, step, logf, env)
                if code != 0:
                    extra = _BACKUP_FAIL_HINT if step == "backup" else ""
                    detail = (
                        f"Command exited with code {code}. See the log for output."
                        + (f" {extra}" if extra else "")
                    )
                    _alert(f"Step failed: {step}", detail)
                    _log_line(f"======== cycle end ERROR after {step} ========", logf)
                    exit_code = code
                    break

            if exit_code == 0:
                _log_line("======== cycle end OK ========", logf)
    finally:
        try:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        lock_f.close()

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
