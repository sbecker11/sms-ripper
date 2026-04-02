# reader.py
import os
import re
import sqlite3
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

import config

# Apple's CoreData epoch starts Jan 1, 2001
APPLE_EPOCH_OFFSET = 978307200

# Punctuation variants when ``STOP_REPLY_TEXT`` is the default ``STOP``.
_STOP_CMD_RE = re.compile(r"^stop[.\s!?]*$", re.IGNORECASE)


def plain_text_is_user_stop_command(combined: str) -> bool:
    """
    True when the merged subject+body is only the configured opt-out text (no other words).

    Compared to :data:`config.STOP_REPLY_TEXT` (default ``STOP``). When that value is ``STOP``,
    trailing ``.`` / ``!`` / whitespace-only padding also match.

    Used for the user's own messages (``is_from_me``) to delete the thread after they reply.
    """
    raw = (combined or "").strip()
    if not raw:
        return False
    norm = " ".join(raw.split())
    key = (getattr(config, "STOP_REPLY_TEXT", None) or "STOP").strip() or "STOP"
    key_norm = " ".join(key.split()).lower()
    if norm.lower() == key_norm:
        return True
    if key_norm == "stop":
        return bool(_STOP_CMD_RE.fullmatch(norm))
    return False


# Modern iMessage often stores body in attributedBody while text is NULL/empty.
RICH_ONLY_PLACEHOLDER = (
    "[iMessage rich content only; plaintext missing in chat.db. "
    "Sender is often a short code or PAC blast — classify as education (civic bulk SMS) if applicable.]"
)


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
    subject: str = ""
    is_from_me: bool
    date: datetime | None = None
    attributes: list[str] = Field(default_factory=list)
    attribute_weights: dict[str, float] = Field(default_factory=dict)
    actions_taken: list[str] = Field(default_factory=list)

    def combined_plaintext(self) -> str:
        """Subject + body (MMS subject lines are often empty). Used for rules and classification."""
        s = (self.subject or "").strip()
        t = (self.text or "").strip()
        if s and t:
            return f"{s}\n{t}"
        return s or t

    def display(self) -> str:
        direction = "→ ME" if self.is_from_me else f"← {self.sender or self.chat_identifier}"
        ts = self.date.strftime("%Y-%m-%d %H:%M:%S") if self.date else "unknown"
        head = self.combined_plaintext()
        return f"[{ts}] {direction}: {head[:120]}"


def get_recent_messages(
    limit: int = config.MESSAGE_FETCH_LIMIT,
    lookback_minutes: int = config.LOOKBACK_MINUTES,
    inbound_only: bool = True,
) -> list[Message]:
    """
    Pull recent inbound messages from chat.db.
    Skips reactions/tapbacks (associated_message_type != 0).
    Includes rows with empty text but non-empty attributedBody (common on recent macOS).
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

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        msg_cols = {row[1] for row in conn.execute("PRAGMA table_info(message)").fetchall()}
        has_attr_body = "attributedBody" in msg_cols
        has_assoc = "associated_message_type" in msg_cols
        has_subject = "subject" in msg_cols
        subj_nonempty = (
            "(message.subject IS NOT NULL AND TRIM(message.subject) != '')"
            if has_subject
            else "0"
        )
        subject_select = (
            "COALESCE(NULLIF(TRIM(message.subject), ''), '')"
            if has_subject
            else "''"
        )

        if has_attr_body:
            has_plain = (
                "(message.text IS NOT NULL AND TRIM(message.text) != '')"
            )
            has_rich = (
                "(message.attributedBody IS NOT NULL "
                "AND length(message.attributedBody) > 0)"
            )
            body_clause = f"(({has_plain}) OR ({has_rich}) OR ({subj_nonempty}))"
            esc = RICH_ONLY_PLACEHOLDER.replace("'", "''")
            subj_case = (
                f"WHEN {subj_nonempty} THEN TRIM(message.subject) "
                if has_subject
                else ""
            )
            text_select = (
                "CASE WHEN COALESCE(TRIM(message.text), '') != '' "
                "THEN TRIM(message.text) "
                f"{subj_case}"
                f"ELSE '{esc}' END"
            )
        else:
            if has_subject:
                body_clause = (
                    f"((message.text IS NOT NULL AND TRIM(message.text) != '') "
                    f"OR ({subj_nonempty}))"
                )
            else:
                body_clause = (
                    "message.text IS NOT NULL AND TRIM(message.text) != ''"
                )
            if has_subject:
                text_select = (
                    "CASE WHEN COALESCE(TRIM(message.text), '') != '' "
                    "THEN TRIM(message.text) "
                    "WHEN message.subject IS NOT NULL AND TRIM(message.subject) != '' "
                    "THEN TRIM(message.subject) "
                    "ELSE '' END"
                )
            else:
                text_select = "message.text"

        assoc_clause = ""
        if has_assoc:
            assoc_clause = "AND IFNULL(message.associated_message_type, 0) = 0"

        query = f"""
            SELECT
                message.rowid,
                chat.rowid          AS chat_id,
                chat.chat_identifier,
                handle.id           AS sender,
                {text_select}       AS text,
                {subject_select}    AS subject,
                message.is_from_me,
                message.date
            FROM message
            JOIN chat_message_join  ON message.rowid = chat_message_join.message_id
            JOIN chat               ON chat_message_join.chat_id = chat.rowid
            LEFT JOIN handle        ON message.handle_id = handle.rowid
            WHERE {body_clause}
              {assoc_clause}
              AND message.date >= {cutoff_ts}
              {inbound_filter}
            ORDER BY message.date DESC
            LIMIT {limit}
        """

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
                subject=row["subject"] or "",
                is_from_me=bool(row["is_from_me"]),
                date=apple_ts_to_datetime(row["date"]),
            )
        )

    return messages


def get_recent_outbound_stop_replies(
    limit: int = config.MESSAGE_FETCH_LIMIT,
    lookback_minutes: int = config.LOOKBACK_MINUTES,
) -> list[Message]:
    """
    Recent **outbound** rows whose plaintext is STOP-only (same lookback as inbound fetch).

    Fetches a wider candidate window then filters in Python so STOP in ``text`` / subject matches;
    capped so short-code threads with lots of traffic do not scan unbounded rows.
    """
    db_path = config.CHAT_DB_PATH
    if not os.path.exists(db_path):
        raise FileNotFoundError(
            f"chat.db not found at {db_path}. "
            "Ensure Full Disk Access is granted to your terminal/IDE."
        )

    cutoff_dt = datetime.utcnow() - timedelta(minutes=lookback_minutes)
    cutoff_ts = datetime_to_apple_ts(cutoff_dt)
    fetch_cap = min(max(limit * 25, limit), 500)

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        msg_cols = {row[1] for row in conn.execute("PRAGMA table_info(message)").fetchall()}
        has_attr_body = "attributedBody" in msg_cols
        has_assoc = "associated_message_type" in msg_cols
        has_subject = "subject" in msg_cols
        subj_nonempty = (
            "(message.subject IS NOT NULL AND TRIM(message.subject) != '')"
            if has_subject
            else "0"
        )
        subject_select = (
            "COALESCE(NULLIF(TRIM(message.subject), ''), '')"
            if has_subject
            else "''"
        )

        if has_attr_body:
            has_plain = "(message.text IS NOT NULL AND TRIM(message.text) != '')"
            has_rich = (
                "(message.attributedBody IS NOT NULL "
                "AND length(message.attributedBody) > 0)"
            )
            body_clause = f"(({has_plain}) OR ({has_rich}) OR ({subj_nonempty}))"
            esc = RICH_ONLY_PLACEHOLDER.replace("'", "''")
            subj_case = (
                f"WHEN {subj_nonempty} THEN TRIM(message.subject) "
                if has_subject
                else ""
            )
            text_select = (
                "CASE WHEN COALESCE(TRIM(message.text), '') != '' "
                "THEN TRIM(message.text) "
                f"{subj_case}"
                f"ELSE '{esc}' END"
            )
        else:
            if has_subject:
                body_clause = (
                    f"((message.text IS NOT NULL AND TRIM(message.text) != '') "
                    f"OR ({subj_nonempty}))"
                )
            else:
                body_clause = (
                    "message.text IS NOT NULL AND TRIM(message.text) != ''"
                )
            if has_subject:
                text_select = (
                    "CASE WHEN COALESCE(TRIM(message.text), '') != '' "
                    "THEN TRIM(message.text) "
                    "WHEN message.subject IS NOT NULL AND TRIM(message.subject) != '' "
                    "THEN TRIM(message.subject) "
                    "ELSE '' END"
                )
            else:
                text_select = "message.text"

        assoc_clause = ""
        if has_assoc:
            assoc_clause = "AND IFNULL(message.associated_message_type, 0) = 0"

        query = f"""
            SELECT
                message.rowid,
                chat.rowid          AS chat_id,
                chat.chat_identifier,
                handle.id           AS sender,
                {text_select}       AS text,
                {subject_select}    AS subject,
                message.is_from_me,
                message.date
            FROM message
            JOIN chat_message_join  ON message.rowid = chat_message_join.message_id
            JOIN chat               ON chat_message_join.chat_id = chat.rowid
            LEFT JOIN handle        ON message.handle_id = handle.rowid
            WHERE {body_clause}
              {assoc_clause}
              AND message.date >= {cutoff_ts}
              AND message.is_from_me = 1
            ORDER BY message.date DESC
            LIMIT {fetch_cap}
        """

        rows = conn.execute(query).fetchall()
    finally:
        conn.close()

    out: list[Message] = []
    for row in rows:
        m = Message(
            rowid=row["rowid"],
            chat_id=row["chat_id"],
            chat_identifier=row["chat_identifier"],
            sender=row["sender"],
            text=row["text"],
            subject=row["subject"] or "",
            is_from_me=bool(row["is_from_me"]),
            date=apple_ts_to_datetime(row["date"]),
        )
        if plain_text_is_user_stop_command(m.combined_plaintext()):
            out.append(m)
            if len(out) >= limit:
                break
    return out
