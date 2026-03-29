# Setup

This project is a macOS **SMS / iMessage agent**: it reads `chat.db`, classifies messages with the Anthropic API, applies rules, and can drive the Messages app via AppleScript. Complete the steps below on the Mac where your Messages data lives.

## Prerequisites

- **macOS** (the agent reads the local iMessage SQLite database and uses AppleScript against the Messages app).
- **Python 3.11+** recommended (the repo uses a `venv` with 3.11 in development).
- **Development shell** (reference): zsh 5.9 on `x86_64-apple-darwin24.0` — any recent zsh/bash on macOS is fine.

## 1. Get the code

Clone or copy this repository onto your machine, then open a terminal in the project root (the directory that contains `main.py` and `requirements.txt`).

## 2. Create and use a virtual environment

Using a virtual environment keeps dependencies isolated from your system Python.

```bash
cd /path/to/sms-ripper
python3 -m venv venv
source venv/bin/activate
```

Confirm you are using the venv’s tools:

```bash
which python
which pip
```

Both should resolve under `venv/bin/`.

To leave the environment later:

```bash
deactivate
```

## 3. Install dependencies

With the venv activated:

```bash
pip install -r requirements.txt
```

This installs:

- **pip** (pinned in `requirements.txt` for reproducible installs).
- **pydantic** and **pydantic-settings** — typed settings and models (`config.py`, `Message`, classifier JSON).
- **pytest** and **pytest-cov** — for automated tests and coverage (see [TESTING.md](TESTING.md)).
- **poethepoet** — run named tasks from `pyproject.toml` (e.g. `poe test`, `poe cov`); see [TESTING.md](TESTING.md).

The runtime agent uses the Python standard library plus `osascript` on the system; classification uses `urllib` to call the Anthropic HTTP API (no separate `anthropic` SDK required).

## 4. Anthropic API key

The classifier needs an **Anthropic API key**, supplied **only** via the project’s `.env` file. Shell `export` and other OS environment variables are **not** read for configuration (so the key does not live in your global environment).

Create or manage keys in the Anthropic Console: [https://console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys).

In the **project root** (same folder as `config.py`), create `.env`:

```bash
ANTHROPIC_API_KEY=sk-ant-api03-...
```

`config.py` loads this file via **pydantic-settings** (`Settings` in `config.py`). Optional overrides for other settings use the same file, e.g. `MESSAGE_FETCH_LIMIT=100` (see field aliases in `config.py`).

Keep `.env` out of version control; it should be listed in `.gitignore`.

## 5. macOS permissions

### Full Disk Access

The reader opens `~/Library/Messages/chat.db` in **read-only** mode. macOS requires **Full Disk Access** for that path.

1. Open **System Settings → Privacy & Security → Full Disk Access**.
2. Enable your **terminal** app (Terminal, iTerm2, Warp, etc.).
3. If you run the agent from **Cursor** (or another IDE), add that app as well.

Without this, you may see errors such as “chat.db not found” or permission failures even though the file exists.

Example **read-only SQL** against `chat.db` (counts, recent messages, search): [QUERIES.md](QUERIES.md).

#### Full Disk Access — `launchd` background daemon

If you use the **LaunchAgent** (`poe daemon-install-15m`), macOS runs **`venv/bin/python`**, not your terminal app. You must add the **framework Python** paths, **`/bin/cp`**, **`/usr/bin/sqlite3`**, and related entries — not only Terminal/Cursor. Guided setup: **`poe fda-assist`**. Check access: **`poe verify-fda`**.

Full list of paths, install/stop commands, and log file locations: **[DAEMON.md](DAEMON.md)**.

### Messages and automation

`actions.py` runs **AppleScript** (`osascript`) to send replies and delete threads. You may be prompted to allow **Automation** (e.g. Terminal or **Cursor** controlling **Messages**). Approve those prompts for the app you use to run `python main.py` or **`poe quit-messages`**. If AppleScript “does nothing,” check **System Settings → Privacy & Security → Automation** for your terminal/IDE.

**Accessibility** may be mentioned for richer UI automation; AppleScript against Messages still depends on what macOS allows for your runner app. If something fails, check **System Settings → Privacy & Security** for related toggles.

### Writes to `chat.db` (archive)

The **`archive`** action copies a row into `<TAG>_archive` and removes the live **`message`** row. That opens **`chat.db` for writing**. Quit the **Messages.app GUI** first so the DB is not locked — the agent prompts when needed; you can also run **`poe quit-messages`** beforehand. Take a copy with **`poe backup-db`** (writes under **`backups/`** in the project root; see `scripts/backup_chat_db.py`).

### Blocklist caveat

**Political runs** (`main.py` default policy) do not read or write `blocked_senders.txt`. The **`--policy spam`** path may still append to that file when the **`block`** action runs; it does not cause `main.py` to skip senders on the next run.

## 6. Configuration (`config.py`)

You can change defaults in code or override them in **`.env`** (same keys as in `config.py`, e.g. `MESSAGE_FETCH_LIMIT`):

| Setting               | Purpose                                                                                |
| --------------------- | -------------------------------------------------------------------------------------- |
| `MESSAGE_FETCH_LIMIT` | Max messages per run                                                                   |
| `LOOKBACK_MINUTES`    | Only messages newer than this window                                                   |
| `CHAT_DB_PATH`        | Path to `chat.db` (default: `~/Library/Messages/chat.db`)                              |
| `STOP_REPLY_TEXT`     | Text sent for the `send_stop` action (default: `STOP`)                                 |
| `DRY_RUN`             | When `True`, no real sends / archive / deletes (set by `python main.py --dry-run`)   |
| `LOG_FILE`            | Path to the agent log file (default: `sms_agent.log` in the current working directory) |

## 7. Run the agent

From the project root, with the venv activated and your key available:

```bash
# Agent preview — same filters as a real run: inbound-only, lookback window.
# Logs Attributes, Matched rules, Actions; no archive / send_stop / delete / block side effects.
python main.py --dry-run

# If you see "No new messages found.", widen the window (minutes) or raise --limit.
python main.py --dry-run --lookback 1440 --limit 100

# Single run (defaults: lookback and limit from config)
python main.py

# Poll every N seconds
python main.py --loop 120

# Wider window / more messages
python main.py --lookback 360 --limit 200
```

**Richer preview (read-only `chat.db`):** To print recent rows **inbound + outbound** (latest by date) with **Attributes**, **Matched rules**, and **Actions (execution)** — without the agent’s lookback filter — use **`poe preview-recent`** or **`poe preview-recent-compact`** (see [QUERIES.md](QUERIES.md)). **`\*-offline`** variants skip Claude and use **`UNKNOWN`** tags only.

**Working directory:** Logs, `blocked_senders.txt`, and **`backups/`** (from `poe backup-db`) are created relative to the **current working directory** unless you change paths in `config.py`. Running from the project root is the simplest approach; **`poe echo-cd-root`** prints a copy-pasteable `cd` to the repo root.

## 8. Optional: verify database access

If setup is correct and Full Disk Access is granted, Python should be able to open the DB read-only. If `main.py` or tests that use a real path fail, re-check Full Disk Access for the exact binary launching Python (terminal app, IDE, or `python` from the venv).

For the **daemon**, run **`poe verify-fda`** from the project root (uses `venv/bin/python`). That probes **`chat.db`** reads the same way **`backup_chat_db.py`** and **`main.py`** do. See [DAEMON.md](DAEMON.md).

## 9. Optional: tab completion for `poe`

[Poe the Poet](https://github.com/nat-n/poethepoet) can complete **task names** (e.g. `query-total`, `cov`) and **global flags** (`-C`, `-v`, …). Generate the script with the same `poe` you use day to day — with the venv activated so `which poe` points at `venv/bin/poe`.

### zsh (default on macOS)

**Without Oh My Zsh:**

```bash
mkdir -p ~/.zfunc
poe _zsh_completion > ~/.zfunc/_poe
```

Add to `~/.zshrc`:

```zsh
fpath+=~/.zfunc
autoload -Uz compinit && compinit
```

**With Oh My Zsh:**

```bash
mkdir -p ~/.oh-my-zsh/completions
poe _zsh_completion > ~/.oh-my-zsh/completions/_poe
```

Open a new terminal. If completions look stale after upgrading `poethepoet`, run `rm ~/.zcompdump*` and restart zsh.

### After you edit `pyproject.toml` tasks

You **do not** need to re-run `poe _zsh_completion` just because you added or renamed tasks. The `_poe` script you installed is generic; it asks `poe` for the current task list when you tab-complete.

**zsh** caches that list (on the order of **~1 hour** and/or after several completions in the same session). To see new task names sooner:

- Open a **new terminal** (clears Poe’s in-memory cache), and/or  
- Delete Poe’s completion cache files, e.g.  
  `rm -f ~/.zcompcache/*poe*`  
  (if that directory is empty or missing, you only needed the new shell).

Re-run `poe _zsh_completion > …` when you **upgrade** the `poethepoet` package itself, in case the completion script format changed.

**bash:** start a new shell or `source ~/.bashrc` if completions feel stale; re-run `poe _bash_completion` (or reinstall the completion file) after upgrading `poethepoet`.

### bash

**Quick (add to `~/.bashrc`):**

```bash
eval "$(poe _bash_completion)"
```

**Or install a completion file:**

```bash
mkdir -p ~/.local/share/bash-completion/completions
poe _bash_completion > ~/.local/share/bash-completion/completions/poe
```

You may need the `bash-completion` package and a line in `~/.bashrc` that sources user completions (varies by OS).

More detail: [Poe the Poet — Installation](https://poethepoet.natn.io/installation.html).

---

For automated tests and coverage, see [TESTING.md](TESTING.md).
