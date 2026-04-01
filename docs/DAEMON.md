# Background daemon (launchd)

System architecture (tag catalog, policies, archive) is summarized in [FRAMEWORK.md](FRAMEWORK.md).

The **LaunchAgent** `com.smsripper.periodic` runs [`scripts/daemon_cycle.py`](../scripts/daemon_cycle.py) on a fixed interval (default **900 seconds = 15 minutes**) and at **login**. Each cycle is the same *shape* as a manual **`poe political-all`** plus badge cleanup:

1. **`backup_chat_db.py`** — timestamped copy under `backups/`
2. **`quit_messages.sh`** — quit Messages.app if running (needed before archive writes)
3. **`main.py --quiet --lookback 10080 --limit 500`** — political policy, wide window
4. **`bulk_mark_read.py --keep-unread 0 --fix-joined-outbound-read`**
5. **`fix_messages_badge_stuck.sh`** — restart Notification Center / Dock so badges refresh
6. **`generate_report_html.py`** — writes **`reports/index.html`** (static political-archive report). Failures are logged but do **not** fail the whole cycle.
7. **`generate_daemon_cycles_html.py`** — parses **`logs/daemon.log`** into **`reports/daemon-cycles/index.html`** plus one page per **closed** cycle (see cycle markers below). Runs **after** the cycle-end line is written so the latest run appears in the index. A non-zero exit is logged as a warning only.

**Cycle markers** (same `pid` on start and end):

- `{ts} ======== cycle start pid=<PID> ========`
- `{ts} ======== cycle end pid=<PID> OK ========`
- `{ts} ======== cycle end pid=<PID> ERROR after <step> ========`

Older logs may only have `cycle end OK` / `cycle end ERROR after …` without `pid=`; those are still paired with the **current** open cycle when parsed.

**Tradeoff:** Messages is quit every cycle that reaches step 2; the main step uses the **Claude API** (cost). Use a longer interval or uninstall if that is too disruptive.

**Install output:** [`scripts/install_sms_ripper_launchagent.sh`](../scripts/install_sms_ripper_launchagent.sh) writes:

`~/Library/LaunchAgents/com.smsripper.periodic.plist`

The plist’s `ProgramArguments` use **`venv/bin/python`** and **`WorkingDirectory`** = your repo root (absolute path at install time). If you **move the repo** or **recreate the venv**, run **`poe daemon-uninstall`** then **`poe daemon-install-15m`** again.

---

## 1. Full Disk Access (required for the daemon)

`launchd` runs **`repo/venv/bin/python`**, not Terminal or Cursor. **Granting FDA only to your terminal is not enough** for scheduled runs.

### Why several paths are listed

- **TCC (Full Disk Access)** is tied to the **executable that opens the protected file**. Homebrew Python is often reached through **symlinks**; macOS may match **`Python.framework/.../Python.app/Contents/MacOS/Python`** rather than **`bin/python3.11`** alone.
- **Backup** can use **`/bin/cp -p`** as a fallback so **`/bin/cp`** should be allowed.
- **`/usr/bin/sqlite3`** is used as another backup fallback.
- **`main.py` / `reader`** still open **`chat.db` in Python**, so the **framework Python** paths must be allowed for classification to work.

### Step-by-step (recommended)

1. From the **repo root**, with venv available:

   ```bash
   poe fda-assist
   ```

   This opens **System Settings → Full Disk Access** (best-effort), then copies **each path** to the clipboard in order. For each path: **+** → **Cmd+Shift+G** → **Cmd+V** → **Open** → turn the switch **ON** → **Enter** in the terminal for the next path.

2. Print the same list without the wizard:

   ```bash
   poe daemon-fda-path
   ```

3. **Verify** that reads actually work (ground truth; Apple does not expose a public “list FDA grants” API):

   ```bash
   poe verify-fda
   ```

   Use the **same** interpreter the plist uses (above uses `poe` → `venv/bin/python`). Optional:

   ```bash
   ./venv/bin/python scripts/verify_fda.py --chat-db "$HOME/Library/Messages/chat.db"
   ```

4. **Automation → Messages** ( **System Settings → Privacy & Security → Automation** ): allow your **terminal** (for manual runs) and ensure **`venv/bin/python`** or **Python** appears if macOS lists it, for **`quit_messages`** / Dock scripts.

5. If access still fails after adding entries: **toggle each FDA row off and on**, quit System Settings, then **log out/in** or **restart**.

### Other permissions

- **Accessibility** — only if you use experimental UI helpers (e.g. `poe messages-scrub-ui`). The daemon cycle does not require it for the core steps.
- **Full Disk Access for Terminal/Cursor** — still useful for **manual** `poe` / `python` from that app; it does **not** replace FDA for **`venv/bin/python`** under **launchd**.

---

## 2. Start, single cycle, stop, status

All commands are run from the **repository root** (same directory as `pyproject.toml`).

| Task | Command | Notes |
|------|---------|--------|
| **Install / enable** (15 min interval) | `poe daemon-install-15m` | Unloads any old job, writes plist, `launchctl bootstrap`. |
| **Install** (custom interval, seconds ≥ 60) | `bash scripts/install_sms_ripper_launchagent.sh install 1800` | Example: 30 minutes. |
| **Run one cycle now** (foreground) | `poe daemon-cycle-once` | Same steps as the agent; logs to `logs/daemon.log`. Does not change the schedule. |
| **Stop / uninstall** | `poe daemon-uninstall` | Unloads job and removes `~/Library/LaunchAgents/com.smsripper.periodic.plist`. |
| **Status** | `poe daemon-status` | Whether the job is loaded; prints last lines of `logs/daemon.log` if present. |
| **Daemon log (tail)** | `poe daemon-log` | Prints the **last ~200 lines** of `logs/daemon.log` (full stream follow: `tail -f logs/daemon.log` in a terminal). |
| **Regenerate cycle HTML** | `poe daemon-cycles-generate` | Rebuilds **`reports/daemon-cycles/`** from `logs/daemon.log` (also runs automatically at end of each daemon cycle). Index lists the **50** newest cycles by default; override with `python scripts/generate_daemon_cycles_html.py --max-cycles N`. |
| **Open cycle index** | `poe daemon-cycles-open` or **`poe daemon-cycle-index`** | macOS **`open`** for **`reports/daemon-cycles/index.html`** (`file://` bookmark). |

**After install:** the next cycle also runs **at login** (`RunAtLoad` in the plist).

**Related (manual, not the daemon):**

- `poe political-all` — backup, quit Messages, one wide political run (no fixed schedule).
- `poe political-done` — `fix-badge` + `badge-diagnose` after a political run.

---

## 3. Daemon log browser (`reports/daemon-cycles/`)

| What | Detail |
|------|--------|
| **Index** | **`reports/daemon-cycles/index.html`** — **latest cycle** (pid + start time + link) at the top; table of all cycles **newest first** |
| **Per cycle** | **`reports/daemon-cycles/cycle_<start_ts>_<pid>.html`** — full log lines for that cycle only (timestamps in filenames use `-` instead of `:`) |
| **Source** | Parsed from **`logs/daemon.log`**; old `cycle_*.html` files are removed and rewritten each run so the folder matches the log |
| **Manual** | **`poe daemon-cycles-generate`** · **`poe daemon-cycles-open`** (macOS) |

---

## 4. Static HTML report (`reports/index.html`)

Each daemon cycle (step 6) regenerates a **self-contained** political-archive HTML file you can open in a browser.

| What | Detail |
|------|--------|
| **Path** | **`reports/index.html`** (under the repo root; `reports/*.html` is gitignored) |
| **Refresh** | The page includes a **meta refresh** every **900 seconds** while the tab stays open, so the browser reloads the file from disk after the next daemon run overwrites it |
| **Open / bookmark** | **`file://`** path to **`reports/index.html`**, or **`poe report-open`** (macOS) after **`poe report-generate`**. Self-contained HTML; **no web server**. |
| **Manual** | **`poe report-generate`** anytime (same script the daemon runs) |

The report lists **`message_tags_archive`** rows (newest first). It does **not** replace Messages; it is read-only.

---

## 5. Log files

| Log | Location | What it contains |
|-----|----------|------------------|
| **Daemon cycle log** | **`logs/daemon.log`** (under repo root) | **All** stdout/stderr from each step in `daemon_cycle.py` (backup, quit, main, bulk_mark_read, badge script), plus timestamped `START` / `OK` / `FAIL` lines. **Tail while testing:** `tail -f logs/daemon.log` |
| **Agent / classifier log** | **`sms_agent.log`** (repo root by default; see `LOG_FILE` in `.env` / `config.py`) | **`main.py`** logging (`logging` module): run banner, classification, errors. When the daemon runs `main.py`, those lines are **also** duplicated here because the root logger writes to this file. |
| **LaunchAgent stdout/stderr** | **Discarded** (`/dev/null` in the plist) | Intentional: the cycle script redirects everything to **`logs/daemon.log`**. |

`logs/*.log` is **gitignored**; `logs/.gitkeep` keeps the directory in the repo.

**On failure:** `daemon_cycle.py` shows a **macOS alert** with the failed step and points at **`logs/daemon.log`**.

---

## 6. Quick troubleshooting

| Symptom | What to try |
|---------|-------------|
| Backup / main fail with permission errors | `poe verify-fda`; add missing paths from `poe daemon-fda-path`; ensure **`/bin/cp`** and **Python.app** are in FDA. |
| Job not running | `poe daemon-status`; reinstall with `poe daemon-install-15m`. |
| Wrong Python / moved repo | `poe daemon-uninstall` then `poe daemon-install-15m`. |
| Badge vs DB disagree | `poe badge-diagnose`; `poe fix-badge`. iCloud/iPhone can still differ from local SQLite. |
