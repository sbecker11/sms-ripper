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

### Messages and automation

`actions.py` runs **AppleScript** (`osascript`) to send replies and delete threads. You may be prompted to allow **Automation** (e.g. Terminal controlling Messages). Approve those prompts for the app you use to run `python main.py`.

**Accessibility** may be mentioned for richer UI automation; AppleScript against Messages still depends on what macOS allows for your runner app. If something fails, check **System Settings → Privacy & Security** for related toggles.

### Blocklist caveat

True “Block Contact” in Messages is not fully replicated by AppleScript alone. The agent appends identifiers to `blocked_senders.txt` and skips them on later runs; for a full block, use Messages → conversation → Details → Block Contact when needed.

## 6. Configuration (`config.py`)

You can change defaults in code or override them in **`.env`** (same keys as in `config.py`, e.g. `MESSAGE_FETCH_LIMIT`):

| Setting               | Purpose                                                                                |
| --------------------- | -------------------------------------------------------------------------------------- |
| `MESSAGE_FETCH_LIMIT` | Max messages per run                                                                   |
| `LOOKBACK_MINUTES`    | Only messages newer than this window                                                   |
| `CHAT_DB_PATH`        | Path to `chat.db` (default: `~/Library/Messages/chat.db`)                              |
| `STOP_REPLY_TEXT`     | Text sent for the `send_stop` action (default: `STOP`)                                 |
| `DRY_RUN`             | When `True`, no send/block/delete (also set by `python main.py --dry-run`)             |
| `LOG_FILE`            | Path to the agent log file (default: `sms_agent.log` in the current working directory) |

## 7. Run the agent

From the project root, with the venv activated and your key available:

```bash
# Preview only — no sends, deletes, or real AppleScript side effects beyond logging.
# Logs each message’s matched rule names (e.g. Matched rules: ['spam_stop']) and merged actions.
python main.py --dry-run

# Single run (defaults: lookback and limit from config)
python main.py

# Poll every N seconds
python main.py --loop 120

# Wider window / more messages
python main.py --lookback 360 --limit 200
```

**Working directory:** Logs and `blocked_senders.txt` are created relative to the **current working directory** unless you change `config.py`. Running from the project root is the simplest approach.

## 8. Optional: verify database access

If setup is correct and Full Disk Access is granted, Python should be able to open the DB read-only. If `main.py` or tests that use a real path fail, re-check Full Disk Access for the exact binary launching Python (terminal app, IDE, or `python` from the venv).

---

For automated tests and coverage, see [TESTING.md](TESTING.md).
