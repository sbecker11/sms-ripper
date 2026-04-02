from __future__ import annotations

"""
Per-database tag vocabulary. Tags are **user-defined** rows (lowercase keys), not a fixed
global enum. ``DEFAULT_TAG_ROWS`` seeds common SMS buckets; add rows in the catalog UI or
here as needed—keep ``rules.py`` / ``classifier.py`` heuristics consistent.
"""

import re
import sqlite3
from pathlib import Path

TABLE_TAG_CATALOG = "sms_ripper_tag_catalog"
# Lowercase keys in DB; flexible but safe for SQL identifiers as unquoted literals.
TAG_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,62}$")

# Default seed: high-volume SMS categories. ``education``, ``church``, and ``sofi`` are
# archive-enabled for ``rules.py`` political rules; everything else is active for classification
# only unless you enable archive in the catalog UI.
DEFAULT_TAG_ROWS: tuple[tuple[str, int, int], ...] = (
    ("education", 1, 1),  # civic / PAC-style bulk; ``rules.py`` political archive
    ("church", 1, 1),  # ward/stake programs, sacrament announcements, religious bulk
    ("sofi", 1, 1),  # SoFi app/bank fraud alerts, spend verifications, account SMS
    ("personal", 1, 0),  # 1:1 from someone you know
    ("transactional", 1, 0),  # OTP/2FA, banks, shipping, appointments, receipts
    ("promo", 1, 0),  # marketing / deals (``rules.py`` promo_only)
    ("social", 1, 0),  # social apps: alerts, invites, “someone commented”, etc.
    ("spam", 1, 0),  # unsolicited junk, phishing, and cold outreach (single bucket)
    ("stop", 1, 0),  # opt-out / STOP intent
    ("unknown", 1, 0),
)


def normalize_tag(tag: str) -> str:
    return (tag or "").strip().lower()


def canonical_tag(tag: str) -> str:
    """Canonical form for tags everywhere: lowercase string."""
    return normalize_tag(tag)


def validate_new_tag_key(raw: str) -> str:
    s = normalize_tag(raw)
    if not s or not TAG_KEY_RE.fullmatch(s):
        raise ValueError(
            "Invalid tag: use lowercase letters, digits, underscore; 1–63 chars; "
            "must start with letter or digit."
        )
    return s


def list_catalog_rows(conn: sqlite3.Connection) -> list[dict[str, object]]:
    """All catalog rows (active and inactive), stable order."""
    ensure_tag_catalog(conn)
    rows = conn.execute(
        f"""
        SELECT tag, active, archive_enabled
        FROM {TABLE_TAG_CATALOG}
        ORDER BY rowid
        """
    ).fetchall()
    out: list[dict[str, object]] = []
    for r in rows:
        key = normalize_tag(str(r[0]))
        if not key:
            continue
        out.append(
            {
                "tag": key,
                "tag_key": key,
                "active": bool(int(r[1] or 0)),
                "archive_enabled": bool(int(r[2] or 0)),
            }
        )
    return out


def count_active_tags(conn: sqlite3.Connection) -> int:
    ensure_tag_catalog(conn)
    row = conn.execute(
        f"SELECT COUNT(*) FROM {TABLE_TAG_CATALOG} WHERE active = 1"
    ).fetchone()
    return int(row[0] or 0)


def upsert_tag_row(
    conn: sqlite3.Connection,
    tag_raw: str,
    *,
    active: bool = True,
    archive_enabled: bool = False,
) -> str:
    """Insert or replace flags for a tag key. Returns normalized key."""
    key = validate_new_tag_key(tag_raw)
    ensure_tag_catalog(conn)
    conn.execute(
        f"""
        INSERT INTO {TABLE_TAG_CATALOG} (tag, active, archive_enabled)
        VALUES (?, ?, ?)
        ON CONFLICT(tag) DO UPDATE SET
            active = excluded.active,
            archive_enabled = excluded.archive_enabled
        """,
        (key, int(bool(active)), int(bool(archive_enabled))),
    )
    return key


def set_tag_flags(
    conn: sqlite3.Connection,
    tag_raw: str,
    *,
    active: bool | None = None,
    archive_enabled: bool | None = None,
) -> None:
    key = normalize_tag(tag_raw)
    if not key:
        raise ValueError("Missing tag")
    ensure_tag_catalog(conn)
    row = conn.execute(
        f"SELECT active, archive_enabled FROM {TABLE_TAG_CATALOG} WHERE tag = ?",
        (key,),
    ).fetchone()
    if not row:
        raise ValueError(f"Unknown tag: {key!r}")
    a = int(row[0] or 0)
    ar = int(row[1] or 0)
    if active is not None:
        new_a = int(bool(active))
        if new_a == 0 and a == 1 and count_active_tags(conn) <= 1:
            raise ValueError("Cannot deactivate the last active tag")
        a = new_a
    if archive_enabled is not None:
        ar = int(bool(archive_enabled))
    conn.execute(
        f"""
        UPDATE {TABLE_TAG_CATALOG}
        SET active = ?, archive_enabled = ?
        WHERE tag = ?
        """,
        (a, ar, key),
    )


def delete_catalog_tag(conn: sqlite3.Connection, tag_raw: str) -> None:
    """
    Remove one row from the tag catalog. Refuses ``unknown`` and the last remaining active tag.
    """
    key = normalize_tag(tag_raw)
    if not key:
        raise ValueError("Missing tag")
    if key == "unknown":
        raise ValueError("Cannot delete reserved tag 'unknown'")
    ensure_tag_catalog(conn)
    row = conn.execute(
        f"SELECT active FROM {TABLE_TAG_CATALOG} WHERE tag = ?", (key,)
    ).fetchone()
    if not row:
        raise ValueError(f"Unknown tag: {key!r}")
    if int(row[0] or 0) == 1 and count_active_tags(conn) <= 1:
        raise ValueError("Cannot delete the last active tag")
    conn.execute(f"DELETE FROM {TABLE_TAG_CATALOG} WHERE tag = ?", (key,))


def ensure_tag_catalog(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_TAG_CATALOG} (
            tag TEXT PRIMARY KEY,
            active INTEGER NOT NULL DEFAULT 1,
            archive_enabled INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    for tag, active, archive_enabled in DEFAULT_TAG_ROWS:
        conn.execute(
            f"""
            INSERT INTO {TABLE_TAG_CATALOG} (tag, active, archive_enabled)
            VALUES (?, ?, ?)
            ON CONFLICT(tag) DO NOTHING
            """,
            (tag, int(active), int(archive_enabled)),
        )


def active_tags(conn: sqlite3.Connection) -> list[str]:
    ensure_tag_catalog(conn)
    rows = conn.execute(
        f"SELECT tag FROM {TABLE_TAG_CATALOG} WHERE active = 1 ORDER BY rowid"
    ).fetchall()
    tags = [canonical_tag(r[0]) for r in rows if normalize_tag(str(r[0]))]
    return tags or ["unknown"]


def archival_tags(conn: sqlite3.Connection) -> set[str]:
    ensure_tag_catalog(conn)
    rows = conn.execute(
        f"""
        SELECT tag
        FROM {TABLE_TAG_CATALOG}
        WHERE active = 1 AND archive_enabled = 1
        """
    ).fetchall()
    return {canonical_tag(r[0]) for r in rows if normalize_tag(str(r[0]))}


def active_tags_from_db(db_path: str) -> list[str]:
    path = Path(db_path).expanduser()
    if not path.exists():
        return [canonical_tag(t[0]) for t in DEFAULT_TAG_ROWS]
    conn = sqlite3.connect(path, timeout=15.0)
    try:
        return active_tags(conn)
    finally:
        conn.close()

