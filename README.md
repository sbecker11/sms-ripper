# SMS Agent

An AI-powered iMessage agent that reads recent messages, classifies them using Claude,
applies configurable rules, and takes action. By default, **only messages tagged POLITICAL**
(non-personal) are actioned (**archive** only — copied to `POLITICAL_archive` and removed from the live `message` table so they disappear from Messages); other tags (e.g. SPAM alone)
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

**Optional — ghost unread rows:** If the sidebar shows unread threads that say **“No Conversation Selected”** until you click twice, you can try the experimental UI scrub (requires Accessibility): `poe messages-scrub-ui -- --rows 30` (see `scripts/messages_scrub_sidebar.py --help`). This is **fragile** across macOS versions; database cleanup (`political-all`, `bulk_mark_read`) remains the reliable approach.

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

## Focus: archiving political texts

If you care about **pulling campaign / PAC SMS out of the live thread**, treat **archive-only political** as the core product. The main pipeline **does not** send STOP, **does not** use `blocked_senders.txt` to skip senders, and **does not** append to the blocklist. **Dock badge tooling** (`scripts/bulk_mark_read.py`, `poe badge-diagnose`, `--mark-read-phase2`) is optional and separate.

**Recommended loop**

1. **Preview** (no writes): `poe dry-run` or `poe dry-run-wide` (wider window / more rows).
2. **Live run** (writes `chat.db`; **Messages must be quit**): `poe political-all` — backs up DB, quits Messages, then runs the agent with a wide lookback.
3. **Where it goes**: qualifying rows are copied into the **`POLITICAL_archive`** table (same columns as `message`), then removed from **`message`** (see `archive.py`).

**What gets archived**

- Classifier must produce **`POLITICAL`** and not **`PERSONAL`** (`rules.py` → `political` rule).
- Matched action is **`archive`** only (no STOP, no blocklist).

**Inspect without acting**: `poe preview-recent` / `poe preview-recent-compact` (see [docs/QUERIES.md](docs/QUERIES.md)).

## Background daemon (launchd)

Scheduled runs (default every **15 minutes** after login) perform the same *kind* of work as **`poe political-all`** plus **badge cleanup**. **Full setup** — Full Disk Access paths, **start / one cycle / stop**, and **log files** — is documented here:

**[docs/DAEMON.md](docs/DAEMON.md)**

Quick reference:

| Task | Command |
|------|---------|
| Guided Full Disk Access (clipboard walkthrough) | `poe fda-assist` |
| Print FDA paths to add | `poe daemon-fda-path` |
| Verify `chat.db` access (Python, `/bin/cp`, `sqlite3`) | `poe verify-fda` |
| Install / enable (15 min) | `poe daemon-install-15m` |
| Run **one** cycle now (foreground) | `poe daemon-cycle-once` |
| Stop / remove LaunchAgent | `poe daemon-uninstall` |
| Loaded? + tail of log | `poe daemon-status` |
| View end of daemon log (~200 lines) | `poe daemon-log` |
| Regenerate static report (`reports/index.html`) | `poe report-generate` |
| Open report in browser (macOS) | `poe report-open` |
| Archive tag training UI (loopback; quit Messages first) | `poe archive-training-server` |
| Regenerate daemon-cycle HTML (index + per-cycle pages) | `poe daemon-cycles-generate` |
| Open cycle index (macOS) | `poe daemon-cycles-open` or `poe daemon-cycle-index` |

**Logs:** **`logs/daemon.log`** (cycle steps); **`sms_agent.log`** (`main.py` logging during the cycle). Details: [docs/DAEMON.md](docs/DAEMON.md) (section *Log files*).

**Static report:** each daemon cycle regenerates **`reports/index.html`** (political archive). **`poe report-generate`** / **`poe report-open`**.

**Classification details:** [docs/CLASSIFICATION.md](docs/CLASSIFICATION.md) — multi-label tags, optional per-tag weights, archive JSON, and training column **W**.

**Archive tag training (local UI):** With Messages quit and **`ANTHROPIC_API_KEY`** set, run **`poe archive-training-server`** (or **`python scripts/archive_training_server.py`**). It binds to **loopback only** (default **http://127.0.0.1:8765**) and **opens Google Chrome** to that URL on macOS (**`--no-browser`** to skip). **`GET /`** serves the same **political archive index** as **`reports/index.html`** (newest first, **`--limit`** rows), plus a **last retrain** column (UTC from the last training-UI **Apply**) and a **Retrained (training UI)** filter so you can avoid redoing the same row. The **full** icon opens the tag editor at **`/message/<rowid>`**. **Apply** re-runs the classifier with your hints, updates **`POLITICAL_archive.classifier_attributes`** (plus small SQLite training tables), then **closes** the message tab and reloads the index tab when you opened it from there. **Done** closes **without** re-running the classifier (same index reload when opened from the index). For a **file://** report that jumps to the server, regenerate with **`--archive-training-url http://127.0.0.1:8765`** while the server is running.

**Daemon log browser:** **`reports/daemon-cycles/index.html`** lists each parsed cycle (latest first, link to full text). **`poe daemon-cycles-generate`** / **`poe daemon-cycles-open`**. See [docs/DAEMON.md](docs/DAEMON.md).

## How It Works

```
chat.db → reader.py → classifier.py (Claude API) → rules.py → actions.py
```

1. **reader.py** — reads `~/Library/Messages/chat.db` in read-only mode
2. **classifier.py** — sends each message to Claude Sonnet, gets back **multi-label** tags plus optional **per-tag weights** in **[0, 1]**; rules see the tag list after an optional **confidence threshold** (see [docs/CLASSIFICATION.md](docs/CLASSIFICATION.md)). Allowed tags:
   `SPAM | STOP | SCAM | POLITICAL | PROMO | LEGIT | PERSONAL | UNKNOWN`
3. **rules.py** — maps attribute combinations to actions (merge order when multiple rules match)
4. **actions.py** — executes: `send_stop`, `block`, `delete`, `archive`, `log_only`. **Execution order** is normalized so **`archive` always runs before `delete`** even if rule merge order differs.

## Customizing Rules

Edit `rules.py` to add or modify rules. Each rule has:
- `condition`: a lambda that takes the attributes list and returns True/False
- `actions`: list of `send_stop`, `block`, `delete`, `archive`, `log_only`

Example — archive political messages only (quit Messages before runs that touch `chat.db`; see `actions.py` for the quit guard):

```python
Rule(
    name="political",
    condition=lambda attrs: "POLITICAL" in attrs and "PERSONAL" not in attrs,
    actions=["archive"],
)
```

`poe preview-recent` shows **`Actions (rule merge)`** vs **`Actions (execution)`** when they differ (matches `execute_actions`).

## Notes on Blocking

The **political** policy does not block or maintain `blocked_senders.txt`. If you run **`main.py --policy spam`**, the spam rules may still call **`block`** (append to `blocked_senders.txt`); that file is **not** read by `main.py` anymore, so it does not affect who gets classified or archived.

## Changelog

Recent changes (UTC-timestamped entries, newest first): **[CHANGELOG.md](CHANGELOG.md)**.

## Files

| File | Purpose |
|------|---------|
| `main.py` | Orchestrator / CLI entry point |
| `reader.py` | chat.db reader, Message dataclass |
| `classifier.py` | Claude API classification |
| `archive_tag_training.py` | SQLite helpers + regenerate flow for human-in-the-loop tag review |
| `rules.py` | Rules engine |
| `archive.py` | Copy rows to `<TAG>_archive`, delete live `message` row |
| `actions.py` | AppleScript send/block/delete; dispatches `archive` |
| `config.py` | Configuration |
| `blocked_senders.txt` | Legacy / spam-policy block append target (optional; not used to skip senders in `main.py`) |
| `backups/` | `chat.db` copies from `poe backup-db` (gitignored) |
| `sms_agent.log` | Run log (auto-created) |
| `scripts/dry_run_recent.py` | Preview tags + rules + execution-ordered actions (read-only DB) |
| `scripts/backup_chat_db.py` | Timestamped backup under `backups/` |
| `docs/DAEMON.md` | LaunchAgent: FDA, start/stop, logs, HTML report, troubleshooting |
| `reports/index.html` | Generated political-archive report (gitignored; created by daemon or `poe report-generate`) |
| `reports/daemon-cycles/index.html` | Cycle index + links to per-cycle logs (gitignored; `poe daemon-cycles-generate`) |
| `scripts/daemon_log_cycles.py` | Parse `daemon.log` into cycles (shared parser) |
| `scripts/generate_daemon_cycles_html.py` | Write `reports/daemon-cycles/*.html` |
| `scripts/archive_training_server.py` | Loopback HTTP UI to edit human tag hints and re-run the classifier on an archive row |
| `CHANGELOG.md` | Timestamped summary of recent project changes (UTC) |
| `logs/daemon.log` | Daemon cycle stdout/stderr (gitignored if `*.log`) |
