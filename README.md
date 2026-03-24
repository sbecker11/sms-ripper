# SMS Agent

An AI-powered iMessage agent that reads recent messages, classifies them using Claude,
applies configurable rules, and takes action — sending STOP replies, blocking senders,
and deleting spam threads.

## Setup

```bash
# 1. Clone / copy this folder into your project
# 2. Set your Anthropic API key
export ANTHROPIC_API_KEY=sk-ant-...

# 3. No pip installs needed — uses only Python stdlib + osascript
```

## Permissions Required

In **System Settings → Privacy & Security → Full Disk Access**:
- Add your terminal app (Terminal, iTerm2, Warp)
- Add Cursor (if running from the IDE)

In **System Settings → Privacy & Security → Accessibility**:
- Add your terminal app (required for AppleScript UI automation)

## Usage

```bash
# Dry run — preview what would happen, no actions taken
python main.py --dry-run

# Run once against the last 60 minutes of messages
python main.py

# Run every 2 minutes in the background
python main.py --loop 120

# Look back further / process more messages
python main.py --lookback 360 --limit 200
```

## How It Works

```
chat.db → reader.py → classifier.py (Claude API) → rules.py → actions.py
```

1. **reader.py** — reads `~/Library/Messages/chat.db` in read-only mode
2. **classifier.py** — sends each message to Claude Sonnet, gets back attributes:
   `SPAM | STOP | SCAM | POLITICAL | PROMO | LEGIT | PERSONAL | UNKNOWN`
3. **rules.py** — maps attribute combinations to actions
4. **actions.py** — executes: `send_stop`, `block`, `delete`, `log_only`

## Customizing Rules

Edit `rules.py` to add or modify rules. Each rule has:
- `condition`: a lambda that takes the attributes list and returns True/False
- `actions`: list of `send_stop`, `block`, `delete`, `log_only`

Example — delete all political messages silently:
```python
Rule(
    name="political",
    condition=lambda attrs: "POLITICAL" in attrs,
    actions=["delete"],
)
```

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
| `actions.py` | AppleScript send/block/delete |
| `config.py` | Configuration |
| `blocked_senders.txt` | Local blocklist (auto-created) |
| `sms_agent.log` | Run log (auto-created) |
