# Useful `chat.db` queries

Apple stores iMessage / SMS data in a local SQLite database:

`~/Library/Messages/chat.db`

You need **Full Disk Access** for the terminal or IDE that runs these queries. See [SETUP.md](SETUP.md).

---

## Run from the repo (`poe` / script)

Canonical SQL lives under **`scripts/queries/`**. The wrapper **`scripts/run_chat_query.sh`** opens the DB **read-only** and prints **column headers** for easier reading.

From the **project root** (venv optional for these; `sqlite3` is the macOS CLI):

| Command | What it runs |
|---------|----------------|
| `poe query-total` | Count all rows in `message` |
| `poe query-text` | Count text rows (no tapbacks), same filter as `reader.py` |
| `poe query-directions` | Inbound vs outbound counts |
| `poe query-chats` | Number of conversations |
| `poe query-top-chats` | Top 25 chats by message volume |
| `poe query-recent` | Latest 20 text messages (preview) |
| `poe query-handles` | Up to 100 handle ids |
| `poe query-empty-text` | Count NULL/empty `text` rows |
| `poe query-associated-types` | Breakdown by `associated_message_type` |
| `poe query-orphans` | Messages not linked to any chat |

Direct shell (same as `poe`):

```bash
bash scripts/run_chat_query.sh queries/total_messages.sql
```

**Custom database path** (optional):

```bash
CHAT_DB_PATH="$HOME/Library/Messages/chat.db" poe query-total
```

---

## Manual `sqlite3` (one-liners)

**Prefer read-only access** so the database is never opened for write by tools:

```bash
DB="file:${HOME}/Library/Messages/chat.db?mode=ro"
sqlite3 "$DB" "YOUR_SQL_HERE"
```

Or one-shot:

```bash
sqlite3 "file:${HOME}/Library/Messages/chat.db?mode=ro" <<'SQL'
SELECT COUNT(*) FROM message;
SQL
```

Load a file from the repo (same as `poe` tasks):

```bash
sqlite3 -header -column "file:${HOME}/Library/Messages/chat.db?mode=ro" \
  ".read $(pwd)/scripts/queries/total_messages.sql"
```

---

## Schema cheat sheet (common tables)

| Table | Role |
|-------|------|
| `message` | One row per message (`text`, `date`, `is_from_me`, `handle_id`, `associated_message_type`, …) |
| `handle` | Phone / email identifiers (`id`) |
| `chat` | Conversations (`chat_identifier`, display name fields, …) |
| `chat_message_join` | Links `message.rowid` ↔ `chat.rowid` |
| `chat_handle_join` | Links handles ↔ chats |

`message.date` is stored in **Apple’s nanosecond epoch** (relative to **2001-01-01 UTC**). The SMS agent converts this in `reader.py` (`APPLE_EPOCH_OFFSET` + divide by 1e9).

---

## Counts and inventory

### Total rows in `message`

`scripts/queries/total_messages.sql` — `poe query-total`

```sql
SELECT COUNT(*) AS total_messages FROM message;
```

### Rows with non-empty text, excluding tapbacks / reactions

Same idea as the agent’s reader (`associated_message_type = 0` filters reactions and similar side-effect rows).

`scripts/queries/text_messages_no_tapback.sql` — `poe query-text`

```sql
SELECT COUNT(*) AS text_messages_no_tapback
FROM message
WHERE text IS NOT NULL
  AND text != ''
  AND associated_message_type = 0;
```

### Inbound vs outbound

`is_from_me = 1` means you sent the message.

`scripts/queries/direction_counts.sql` — `poe query-directions`

```sql
SELECT
  CASE WHEN is_from_me = 1 THEN 'from_me' ELSE 'from_them' END AS direction,
  COUNT(*) AS n
FROM message
GROUP BY is_from_me;
```

### Number of conversations (chats)

`scripts/queries/chat_count.sql` — `poe query-chats`

```sql
SELECT COUNT(*) AS chat_count FROM chat;
```

### Messages per chat (top threads by volume)

`scripts/queries/top_chats.sql` — `poe query-top-chats`

```sql
SELECT
  c.chat_identifier,
  COUNT(*) AS message_count
FROM chat_message_join cmj
JOIN chat c ON c.rowid = cmj.chat_id
JOIN message m ON m.rowid = cmj.message_id
WHERE m.text IS NOT NULL AND m.text != '' AND m.associated_message_type = 0
GROUP BY c.rowid
ORDER BY message_count DESC
LIMIT 25;
```

---

## Recent activity

### Latest 20 text messages (with chat + sender)

Matches the project’s join pattern.

`scripts/queries/recent_20.sql` — `poe query-recent`

```sql
SELECT
  m.rowid,
  c.chat_identifier,
  h.id AS sender,
  m.is_from_me,
  m.date,
  substr(m.text, 1, 80) AS text_preview
FROM message m
JOIN chat_message_join cmj ON m.rowid = cmj.message_id
JOIN chat c ON cmj.chat_id = c.rowid
LEFT JOIN handle h ON m.handle_id = h.rowid
WHERE m.text IS NOT NULL
  AND m.text != ''
  AND m.associated_message_type = 0
ORDER BY m.date DESC
LIMIT 20;
```

### Messages in the last 24 hours (raw `date`; approximate without Python conversion)

Apple timestamps are large integers; “last 24h” is easiest from **Python** using `reader.datetime_to_apple_ts`, or sample recent rows ordered by `date DESC` and inspect in code. For a rough SQL-only approach, capture `MAX(date)` then subtract a duration in nanoseconds (error-prone); prefer the app’s lookback logic in `reader.get_recent_messages`.

---

## Senders and search

### Distinct handles you’ve messaged (from `handle`)

`scripts/queries/handles_sample.sql` — `poe query-handles`

```sql
SELECT id FROM handle ORDER BY id LIMIT 100;
```

### Search message text (substring)

No `poe` task (needs your keyword). Example:

```bash
sqlite3 "file:${HOME}/Library/Messages/chat.db?mode=ro" "
SELECT m.rowid, h.id AS sender, substr(m.text, 1, 120) AS preview
FROM message m
LEFT JOIN handle h ON m.handle_id = h.rowid
WHERE m.text LIKE '%KEYWORD%'
  AND m.associated_message_type = 0
ORDER BY m.date DESC
LIMIT 50;
"
```

Replace `KEYWORD` (escape `%` / `_` in SQL if needed).

---

## Debugging / data quality

### Messages with NULL or empty `text` (often non-text payloads)

`scripts/queries/empty_text_count.sql` — `poe query-empty-text`

```sql
SELECT COUNT(*) FROM message WHERE text IS NULL OR text = '';
```

### Rows that look like reactions / associated content (`associated_message_type != 0`)

`scripts/queries/associated_type_breakdown.sql` — `poe query-associated-types`

```sql
SELECT associated_message_type, COUNT(*) AS n
FROM message
GROUP BY associated_message_type
ORDER BY n DESC
LIMIT 20;
```

### Messages not linked to any chat (unusual)

`scripts/queries/orphan_messages.sql` — `poe query-orphans`

```sql
SELECT COUNT(*) AS orphan_messages
FROM message m
WHERE NOT EXISTS (
  SELECT 1 FROM chat_message_join cmj WHERE cmj.message_id = m.rowid
);
```

---

## Related project code

- **`reader.py`** — production query for recent inbound messages, read-only URI, Apple timestamp handling.
- **`config.py`** — `CHAT_DB_PATH` (default `~/Library/Messages/chat.db`); `run_chat_query.sh` uses the same default or `CHAT_DB_PATH`.

For setup, permissions, and API keys, see [SETUP.md](SETUP.md). For tests and other `poe` tasks, see [TESTING.md](TESTING.md).
