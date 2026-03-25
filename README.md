# SMS Agent

An AI-powered iMessage agent that reads recent messages, classifies them using Claude,
applies configurable rules, and takes action. By default, **only messages tagged POLITICAL**
(non-personal) are actioned (**archive**, **STOP**, **blocklist**); other tags (e.g. SPAM alone)
typically **log only** unless you add more rules in `rules.py`.

## Setup

```bash
# 1. Clone / copy this folder into your project
# 2. Create venv and install deps
python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt

# 3. API key: project-root .env only — ANTHROPIC_API_KEY=sk-ant-...
```

See [docs/SETUP.md](docs/SETUP.md) for full setup. Configuration uses **Pydantic** / **pydantic-settings**; the agent still uses stdlib + `osascript` for Messages.

## Permissions Required

In **System Settings → Privacy & Security → Full Disk Access**:
- Add your terminal app (Terminal, iTerm2, Warp)
- Add Cursor (if running from the IDE)

In **System Settings → Privacy & Security → Accessibility**:
- Add your terminal app (required for AppleScript UI automation)

## Usage

```bash
# Agent: dry run — same pipeline as a real run, but no sends / archive / deletes (logs only)
python main.py --dry-run

# Agent: inbound-only, default lookback (see LOOKBACK_MINUTES in .env / config)
python main.py

# Agent: poll every N seconds
python main.py --loop 120

# Agent: wider window (minutes) and more messages per pass
python main.py --lookback 360 --limit 200
```

**Why `dry-run` can show “No new messages”:** `main.py` only loads **inbound** texts in the **lookback** window. For a **read-only** dump of the latest rows from `chat.db` (inbound + outbound) with **Attributes**, **Matched rules**, and **Actions (execution)**, use **`poe preview-recent`** or **`poe preview-recent-compact`** — see [docs/QUERIES.md](docs/QUERIES.md).

**Before any run that writes `chat.db` (e.g. `archive`):** quit Messages, then back up:

```bash
poe quit-messages
poe backup-db
```

Other **`poe`** tasks (queries, repo path): [docs/QUERIES.md](docs/QUERIES.md) and [docs/TESTING.md](docs/TESTING.md).

## How It Works

```
chat.db → reader.py → classifier.py (Claude API) → rules.py → actions.py
```

1. **reader.py** — reads `~/Library/Messages/chat.db` in read-only mode
2. **classifier.py** — sends each message to Claude Sonnet, gets back attributes:
   `SPAM | STOP | SCAM | POLITICAL | PROMO | LEGIT | PERSONAL | UNKNOWN`
3. **rules.py** — maps attribute combinations to actions (merge order when multiple rules match)
4. **actions.py** — executes: `send_stop`, `block`, `delete`, `archive`, `log_only`. **Execution order** is normalized so **`archive` always runs before `delete`** even if rule merge order differs.

## Customizing Rules

Edit `rules.py` to add or modify rules. Each rule has:
- `condition`: a lambda that takes the attributes list and returns True/False
- `actions`: list of `send_stop`, `block`, `delete`, `archive`, `log_only`

Example — archive political messages, send STOP, then record blocklist entry (quit Messages before runs that touch `chat.db`; see `actions.py` for the quit guard):

```python
Rule(
    name="political",
    condition=lambda attrs: "POLITICAL" in attrs and "PERSONAL" not in attrs,
    actions=["archive", "send_stop", "block"],
)
```

`poe preview-recent` shows **`Actions (rule merge)`** vs **`Actions (execution)`** when they differ (matches `execute_actions`).

## Notes on Blocking

Full programmatic blocking (equivalent to Messages → Details → Block Contact) requires
either Accessibility API access or a native Swift/Obj-C bridge. As a pragmatic fallback,
the agent writes blocked senders to `blocked_senders.txt` and skips them on future runs.
To fully block: open Messages, find the thread before deletion, tap Details → Block Contact.

## Files

| File | Purpose |
|------|---------|
| `main.py` | Orchestrator / CLI entry point |
| `reader.py` | chat.db reader, Message dataclass |
| `classifier.py` | Claude API classification |
| `rules.py` | Rules engine |
| `archive.py` | Copy rows to `<TAG>_archive`, delete live `message` row |
| `actions.py` | AppleScript send/block/delete; dispatches `archive` |
| `config.py` | Configuration |
| `blocked_senders.txt` | Local blocklist (auto-created) |
| `backups/` | `chat.db` copies from `poe backup-db` (gitignored) |
| `sms_agent.log` | Run log (auto-created) |
| `scripts/dry_run_recent.py` | Preview tags + rules + execution-ordered actions (read-only DB) |
| `scripts/backup_chat_db.py` | Timestamped backup under `backups/` |
