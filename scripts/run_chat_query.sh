#!/usr/bin/env bash
# Run a .sql file against the local iMessage database (read-only).
# Usage: bash scripts/run_chat_query.sh queries/total_messages.sql
# Override DB path: CHAT_DB_PATH=/path/to/chat.db bash scripts/run_chat_query.sh ...

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REL="${1:?Usage: run_chat_query.sh queries/<file>.sql}"

SQL_PATH="$SCRIPT_DIR/$REL"
if [[ ! -f "$SQL_PATH" ]]; then
  echo "SQL file not found: $SQL_PATH" >&2
  exit 1
fi

DB="${CHAT_DB_PATH:-$HOME/Library/Messages/chat.db}"
if [[ "$DB" == "~"* ]]; then
  DB="$HOME${DB#\~}"
fi

if [[ ! -r "$DB" ]]; then
  echo "Cannot read database: $DB" >&2
  echo "Grant Full Disk Access to this terminal/IDE (see docs/SETUP.md)." >&2
  exit 1
fi

exec sqlite3 -header -column "file:${DB}?mode=ro" ".read \"$SQL_PATH\""
