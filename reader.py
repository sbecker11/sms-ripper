# reader.py
import os
import sqlite3
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

import config

# Apple's CoreData epoch starts Jan 1, 2001
APPLE_EPOCH_OFFSET = 978307200


def apple_ts_to_datetime(ts: int | None) -> datetime | None:
    """Convert Apple nanosecond timestamp to Python datetime."""
    if ts is None or ts == 0:
        return None
    return datetime.utcfromtimestamp(ts / 1e9 + APPLE_EPOCH_OFFSET)


def datetime_to_apple_ts(dt: datetime) -> int:
    """Convert Python datetime to Apple nanosecond timestamp."""
    unix_ts = dt.timestamp() - APPLE_EPOCH_OFFSET
    return int(unix_ts * 1e9)


class Message(BaseModel):
    """One row from chat.db plus fields filled in by the pipeline."""

    model_config = ConfigDict(arbitrary_types_allowed=False)

    rowid: int
    chat_id: int
    chat_identifier: str
    sender: str | None = None
    text: str
    is_from_me: bool
    date: datetime | None = None
    attributes: list[str] = Field(default_factory=list)
    actions_taken: list[str] = Field(default_factory=list)

    def display(self) -> str:
        direction = "→ ME" if self.is_from_me else f"← {self.sender or self.chat_identifier}"
        ts = self.date.strftime("%Y-%m-%d %H:%M:%S") if self.date else "unknown"
        return f"[{ts}] {direction}: {self.text[:120]}"


def get_recent_messages(
    limit: int = config.MESSAGE_FETCH_LIMIT,
    lookback_minutes: int = config.LOOKBACK_MINUTES,
    inbound_only: bool = True,
) -> list[Message]:
    """
    Pull recent inbound messages from chat.db.
    Skips reactions/tapbacks (associated_message_type != 0).
    """
    db_path = config.CHAT_DB_PATH
    if not os.path.exists(db_path):
        raise FileNotFoundError(
            f"chat.db not found at {db_path}. "
            "Ensure Full Disk Access is granted to your terminal/IDE."
        )

    cutoff_dt = datetime.utcnow() - timedelta(minutes=lookback_minutes)
    cutoff_ts = datetime_to_apple_ts(cutoff_dt)

    inbound_filter = "AND message.is_from_me = 0" if inbound_only else ""

    query = f"""
        SELECT
            message.rowid,
            chat.rowid          AS chat_id,
            chat.chat_identifier,
            handle.id           AS sender,
            message.text,
            message.is_from_me,
            message.date
        FROM message
        JOIN chat_message_join  ON message.rowid = chat_message_join.message_id
        JOIN chat               ON chat_message_join.chat_id = chat.rowid
        LEFT JOIN handle        ON message.handle_id = handle.rowid
        WHERE message.text IS NOT NULL
          AND message.text != ''
          AND message.associated_message_type = 0
          AND message.date >= {cutoff_ts}
          {inbound_filter}
        ORDER BY message.date DESC
        LIMIT {limit}
    """

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(query).fetchall()
    finally:
        conn.close()

    messages: list[Message] = []
    for row in rows:
        messages.append(
            Message(
                rowid=row["rowid"],
                chat_id=row["chat_id"],
                chat_identifier=row["chat_identifier"],
                sender=row["sender"],
                text=row["text"],
                is_from_me=bool(row["is_from_me"]),
                date=apple_ts_to_datetime(row["date"]),
            )
        )

    return messages
