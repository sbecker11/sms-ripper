"""
SQLite tables + helpers for human-in-the-loop tag training on message_tags_archive rows.

Used by ``scripts/archive_training_server.py``. Tables are created on first server use.
"""

from __future__ import annotations

import math
import re
import sqlite3
from datetime import datetime, timezone
from typing import Final

import archive as archive_mod
import classifier
import tag_catalog

TABLE_TRAINING = "sms_ripper_archive_tag_training"
TABLE_META = "sms_ripper_archive_training_meta"
TABLE_TAG_GUARDS = "sms_ripper_tag_model_guards"
TABLE_TAG_GUARD_EVENTS = "sms_ripper_tag_model_guard_events"
TABLE_TAG_GUARD_SNAPSHOTS = "sms_ripper_tag_model_guard_snapshots"
TABLE_AUTHOR_TRUST = "sms_ripper_tag_guard_author_trust"


def _archive_table_for_conn(conn: sqlite3.Connection) -> str:
    return archive_mod.require_archive_table(conn, "education")


INCLUDE_BOOST_BASE = 0.30
INCLUDE_BOOST_MULT = 0.55
INCLUDE_MAX = 0.95
RECENCY_HALF_LIFE_DAYS = 90.0

def _training_tags(conn: sqlite3.Connection) -> tuple[str, ...]:
    tags = tuple(tag_catalog.active_tags(conn))
    return tags or TRAINING_TAGS


# Default tag ids (lowercase); live training uses ``_training_tags(conn)``.
TRAINING_TAGS: Final[tuple[str, ...]] = tuple(row[0] for row in tag_catalog.DEFAULT_TAG_ROWS)


def coerce_str_field(value: object | None) -> str:
    """Normalize sqlite values for JSON (TEXT columns may come back as bytes)."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def coerce_apple_timestamp_ns(value: object | None) -> int | None:
    """Apple `message.date` is INTEGER nanoseconds; tolerate str/float from odd schemas."""
    if value is None:
        return None
    if isinstance(value, bytes):
        s_b = value.decode("utf-8", errors="replace").strip()
        if not s_b:
            return None
        try:
            return int(s_b)
        except ValueError:
            try:
                return int(float(s_b))
            except ValueError:
                return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    s = str(value).strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        try:
            return int(float(s))
        except ValueError:
            return None


def _merge_keyword_fields(a: object | None, b: object | None) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for blob in (a, b):
        if not blob:
            continue
        s = coerce_str_field(blob)
        for ln in s.splitlines():
            t = ln.strip()
            if t and t not in seen:
                lines.append(t)
                seen.add(t)
    return "\n".join(lines)


def _tables_with_classifier_attributes_column(conn: sqlite3.Connection) -> list[str]:
    col = archive_mod.CLASSIFIER_ATTRIBUTES_COLUMN
    out: list[str] = []
    for (t,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ):
        if not isinstance(t, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", t):
            continue
        if conn.execute(
            "SELECT 1 FROM pragma_table_info(?) WHERE name = ? LIMIT 1",
            (t, col),
        ).fetchone():
            out.append(t)
    return out


def _rewrite_archive_classifier_blobs_for_merge(
    conn: sqlite3.Connection, old_c: str, new_c: str
) -> int:
    col = archive_mod.CLASSIFIER_ATTRIBUTES_COLUMN
    qcol = archive_mod._quote_ident(col)
    n = 0
    for tbl in _tables_with_classifier_attributes_column(conn):
        qtbl = archive_mod._quote_ident(tbl)
        cur = conn.execute(
            f"SELECT rowid, {qcol} FROM {qtbl} WHERE {qcol} IS NOT NULL AND TRIM({qcol}) != ''"
        )
        for rowid, raw in cur.fetchall():
            blob, changed = classifier.merge_tag_in_classifier_blob(raw, old_c, new_c)
            if changed and blob is not None:
                conn.execute(
                    f"UPDATE {qtbl} SET {qcol} = ? WHERE rowid = ?",
                    (blob, rowid),
                )
                n += 1
    return n


def _merge_training_rows_for_merge(conn: sqlite3.Connection, old_c: str, new_c: str) -> None:
    conn.execute(
        f"""
        DELETE FROM {TABLE_TRAINING}
        WHERE tag = ? AND archive_rowid IN (
            SELECT archive_rowid FROM {TABLE_TRAINING} WHERE tag = ?
        )
        """,
        (old_c, new_c),
    )
    conn.execute(
        f"UPDATE {TABLE_TRAINING} SET tag = ? WHERE tag = ?",
        (new_c, old_c),
    )


def _merge_tag_guards_for_merge(conn: sqlite3.Connection, old_c: str, new_c: str) -> None:
    ro = conn.execute(
        f"SELECT include_keywords, exclude_keywords FROM {TABLE_TAG_GUARDS} WHERE tag = ?",
        (old_c,),
    ).fetchone()
    if not ro:
        return
    rn = conn.execute(
        f"SELECT include_keywords, exclude_keywords FROM {TABLE_TAG_GUARDS} WHERE tag = ?",
        (new_c,),
    ).fetchone()
    now_iso = utc_now_iso()
    if rn:
        inc = _merge_keyword_fields(rn[0], ro[0])
        exc = _merge_keyword_fields(rn[1], ro[1])
        conn.execute(
            f"""
            UPDATE {TABLE_TAG_GUARDS}
            SET include_keywords = ?, exclude_keywords = ?, updated_at = ?
            WHERE tag = ?
            """,
            (inc, exc, now_iso, new_c),
        )
        conn.execute(f"DELETE FROM {TABLE_TAG_GUARDS} WHERE tag = ?", (old_c,))
    else:
        conn.execute(
            f"UPDATE {TABLE_TAG_GUARDS} SET tag = ?, updated_at = ? WHERE tag = ?",
            (new_c, now_iso, old_c),
        )


def _merge_snapshots_for_merge(conn: sqlite3.Connection, old_c: str, new_c: str) -> None:
    rows = conn.execute(
        f"SELECT id, version FROM {TABLE_TAG_GUARD_SNAPSHOTS} WHERE tag = ?",
        (old_c,),
    ).fetchall()
    for sid, ver in rows:
        clash = conn.execute(
            f"""
            SELECT 1 FROM {TABLE_TAG_GUARD_SNAPSHOTS}
            WHERE tag = ? AND version = ? LIMIT 1
            """,
            (new_c, ver),
        ).fetchone()
        if clash:
            conn.execute(
                f"DELETE FROM {TABLE_TAG_GUARD_SNAPSHOTS} WHERE id = ?", (sid,)
            )
        else:
            conn.execute(
                f"UPDATE {TABLE_TAG_GUARD_SNAPSHOTS} SET tag = ? WHERE id = ?",
                (new_c, sid),
            )


def merge_classifier_tag_into(
    conn: sqlite3.Connection, source_tag: str, target_tag: str
) -> int:
    """
    Merge catalog tag **source_tag** (A) into **target_tag** (B): rewrite ``classifier_attributes``
    JSON on every table that has that column, fold training and guard rows into **B**, then
    remove **A** from ``sms_ripper_tag_catalog``. **B** must already exist. Reserved tag
    **unknown** cannot be merged away.

    Returns the number of archive rows whose ``classifier_attributes`` cell was rewritten.
    Caller should **commit**. Uses a savepoint for atomicity.
    """
    ensure_training_tables(conn)
    old_c = tag_catalog.normalize_tag(source_tag)
    new_c = tag_catalog.normalize_tag(target_tag)
    if not old_c or not new_c:
        raise ValueError("source and target tags are required")
    if old_c == new_c:
        return 0
    if old_c == "unknown":
        raise ValueError("Cannot merge away reserved tag 'unknown'")
    if not conn.execute(
        f"SELECT 1 FROM {tag_catalog.TABLE_TAG_CATALOG} WHERE tag = ?", (new_c,)
    ).fetchone():
        raise ValueError(f"Merge target does not exist in catalog: {new_c!r}")
    if not conn.execute(
        f"SELECT 1 FROM {tag_catalog.TABLE_TAG_CATALOG} WHERE tag = ?", (old_c,)
    ).fetchone():
        raise ValueError(f"Unknown source tag: {old_c!r}")
    conn.execute("SAVEPOINT merge_tag_into")
    try:
        n_blob = _rewrite_archive_classifier_blobs_for_merge(conn, old_c, new_c)
        conn.execute(
            f"UPDATE {TABLE_TAG_GUARD_EVENTS} SET tag = ? WHERE tag = ?",
            (new_c, old_c),
        )
        _merge_snapshots_for_merge(conn, old_c, new_c)
        _merge_tag_guards_for_merge(conn, old_c, new_c)
        _merge_training_rows_for_merge(conn, old_c, new_c)
        tag_catalog.delete_catalog_tag(conn, old_c)
        conn.execute("RELEASE SAVEPOINT merge_tag_into")
    except BaseException:
        conn.execute("ROLLBACK TO SAVEPOINT merge_tag_into")
        raise
    return n_blob


def ensure_training_tables(conn: sqlite3.Connection) -> None:
    tag_catalog.ensure_tag_catalog(conn)
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_TRAINING} (
            archive_rowid INTEGER NOT NULL,
            tag TEXT NOT NULL,
            llm_checked INTEGER NOT NULL DEFAULT 0,
            llm_keywords TEXT,
            human_checked INTEGER,
            human_keywords TEXT,
            PRIMARY KEY (archive_rowid, tag)
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_META} (
            archive_rowid INTEGER PRIMARY KEY,
            last_llm_reason TEXT
        )
        """
    )
    _ensure_meta_last_training_regenerate_column(conn)
    _ensure_tag_guard_tables(conn)


def _ensure_meta_last_training_regenerate_column(conn: sqlite3.Connection) -> None:
    names = {r[1] for r in conn.execute(f"PRAGMA table_info({TABLE_META})").fetchall()}
    if "last_training_regenerate_at" not in names:
        conn.execute(
            f"ALTER TABLE {TABLE_META} ADD COLUMN last_training_regenerate_at TEXT"
        )


def _ensure_tag_guard_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_TAG_GUARDS} (
            tag TEXT PRIMARY KEY,
            include_keywords TEXT NOT NULL DEFAULT '',
            exclude_keywords TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_TAG_GUARD_EVENTS} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tag TEXT NOT NULL,
            action TEXT NOT NULL,
            token TEXT NOT NULL,
            author TEXT NOT NULL,
            source_archive_rowid INTEGER,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_TAG_GUARD_SNAPSHOTS} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tag TEXT NOT NULL,
            version INTEGER NOT NULL,
            include_keywords TEXT NOT NULL DEFAULT '',
            exclude_keywords TEXT NOT NULL DEFAULT '',
            author TEXT NOT NULL,
            source_archive_rowid INTEGER,
            created_at TEXT NOT NULL,
            UNIQUE(tag, version)
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_AUTHOR_TRUST} (
            author TEXT PRIMARY KEY,
            trust_score REAL NOT NULL DEFAULT 1.0,
            updated_at TEXT NOT NULL
        )
        """
    )


def fetch_training_regenerate_times(
    conn: sqlite3.Connection, archive_rowids: list[int]
) -> dict[int, str]:
    """Map archive rowid → ISO UTC timestamp of last training-UI Apply, if any."""
    if not archive_rowids:
        return {}
    ensure_training_tables(conn)
    _ensure_meta_last_training_regenerate_column(conn)
    placeholders = ",".join("?" * len(archive_rowids))
    cur = conn.execute(
        f"""
        SELECT archive_rowid, last_training_regenerate_at
        FROM {TABLE_META}
        WHERE archive_rowid IN ({placeholders})
          AND last_training_regenerate_at IS NOT NULL
          AND TRIM(last_training_regenerate_at) != ''
        """,
        archive_rowids,
    )
    out: dict[int, str] = {}
    for row in cur.fetchall():
        rid = int(row[0])
        ts = coerce_str_field(row[1])
        if ts:
            out[rid] = ts
    return out


def heuristic_keywords_political(text: str) -> str:
    """Comma-separated substrings from political markers/patterns that match ``text``."""
    if not text:
        return ""
    try:
        raw_l = text.lower()
        norm = classifier._normalize_for_keyword_scan(text)
        hits: list[str] = []
        for phrase in classifier.CIVIC_EDUCATION_KEYWORD_MARKERS:
            if phrase in raw_l or phrase in norm:
                hits.append(phrase)
        for pat in classifier.CIVIC_EDUCATION_KEYWORD_PATTERNS:
            m = pat.search(text) or pat.search(norm)
            if m and m.group(0) not in hits:
                hits.append(m.group(0))
        return ", ".join(hits[:12])
    except Exception:
        return ""


def _keywords_for_tag(tag: str, text: str) -> str:
    if tag == "education":
        return heuristic_keywords_political(text)
    return ""


def _split_csv_keywords(raw: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for part in (raw or "").split(","):
        tok = part.strip()
        if not tok:
            continue
        key = tok.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(tok)
    return out


def _merge_keyword_tokens(*parts: str) -> str:
    merged: list[str] = []
    seen: set[str] = set()
    for part in parts:
        for tok in _split_csv_keywords(part):
            key = tok.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(tok)
    return ", ".join(merged)


def _parse_human_keywords_guards(raw: str) -> tuple[str, str]:
    """
    Parse per-tag human keywords into include/exclude guard tokens.
    Exclude guard token syntax: prefix token with ``-`` or ``!``.
    """
    include: list[str] = []
    exclude: list[str] = []
    seen_i: set[str] = set()
    seen_e: set[str] = set()
    for part in (raw or "").split(","):
        t = part.strip()
        if not t:
            continue
        if t.startswith(("!", "-")):
            tok = t[1:].strip()
            if not tok:
                continue
            k = tok.lower()
            if k in seen_e:
                continue
            seen_e.add(k)
            exclude.append(tok)
        else:
            k = t.lower()
            if k in seen_i:
                continue
            seen_i.add(k)
            include.append(t)
    return ", ".join(include), ", ".join(exclude)


def _fetch_tag_guards(conn: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    ensure_training_tables(conn)
    training_tags = set(_training_tags(conn))
    cur = conn.execute(
        f"SELECT tag, include_keywords, exclude_keywords FROM {TABLE_TAG_GUARDS}"
    )
    out: dict[str, tuple[str, str]] = {}
    for row in cur.fetchall():
        tag = tag_catalog.normalize_tag(str(row[0]))
        if not tag or tag not in training_tags:
            continue
        out[tag] = (coerce_str_field(row[1]), coerce_str_field(row[2]))
    return out


def _fetch_author_trust_map(conn: sqlite3.Connection) -> dict[str, float]:
    ensure_training_tables(conn)
    out: dict[str, float] = {}
    cur = conn.execute(f"SELECT author, trust_score FROM {TABLE_AUTHOR_TRUST}")
    for row in cur.fetchall():
        author = coerce_str_field(row[0]).strip()
        if not author:
            continue
        try:
            trust = float(row[1])
        except (TypeError, ValueError):
            trust = 1.0
        out[author] = max(0.0, min(2.0, trust))
    return out


def _parse_iso_utc(s: str) -> datetime | None:
    t = (s or "").strip()
    if not t:
        return None
    try:
        if t.endswith("Z"):
            return datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return None


def _recency_decay(created_at_iso: str, now_iso: str) -> float:
    created = _parse_iso_utc(created_at_iso)
    now = _parse_iso_utc(now_iso)
    if created is None or now is None or now <= created:
        return 1.0
    age_days = (now - created).total_seconds() / 86400.0
    if age_days <= 0:
        return 1.0
    return math.pow(0.5, age_days / RECENCY_HALF_LIFE_DAYS)


def _fetch_tag_include_signal(
    conn: sqlite3.Connection, *, now_iso: str
) -> dict[str, float]:
    """
    Per-tag include guard signal in [0, 2], derived from latest snapshot author trust and recency.
    """
    trust_map = _fetch_author_trust_map(conn)
    training_tags = _training_tags(conn)
    out: dict[str, float] = {t: 1.0 for t in training_tags}
    cur = conn.execute(
        f"""
        SELECT s.tag, s.author, s.created_at
        FROM {TABLE_TAG_GUARD_SNAPSHOTS} s
        JOIN (
            SELECT tag, MAX(version) AS max_version
            FROM {TABLE_TAG_GUARD_SNAPSHOTS}
            GROUP BY tag
        ) latest
          ON latest.tag = s.tag AND latest.max_version = s.version
        """
    )
    for row in cur.fetchall():
        tag = tag_catalog.normalize_tag(coerce_str_field(row[0]))
        if not tag or tag not in training_tags:
            continue
        author = coerce_str_field(row[1]).strip()
        created_at = coerce_str_field(row[2]).strip()
        trust = trust_map.get(author, 1.0)
        out[tag] = max(0.0, min(2.0, trust * _recency_decay(created_at, now_iso)))
    return out


def _upsert_tag_guard(
    conn: sqlite3.Connection,
    *,
    tag: str,
    include_keywords: str,
    exclude_keywords: str,
    author: str,
    source_archive_rowid: int | None,
) -> None:
    now = utc_now_iso()
    prev = conn.execute(
        f"SELECT include_keywords, exclude_keywords FROM {TABLE_TAG_GUARDS} WHERE tag = ?",
        (tag,),
    ).fetchone()
    prev_inc = coerce_str_field(prev[0]) if prev else ""
    prev_exc = coerce_str_field(prev[1]) if prev else ""
    conn.execute(
        f"""
        INSERT INTO {TABLE_TAG_GUARDS}
            (tag, include_keywords, exclude_keywords, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(tag) DO UPDATE SET
            include_keywords = excluded.include_keywords,
            exclude_keywords = excluded.exclude_keywords,
            updated_at = excluded.updated_at
        """,
        (tag, include_keywords, exclude_keywords, now),
    )
    _record_guard_events(
        conn,
        tag=tag,
        prev_include=prev_inc,
        prev_exclude=prev_exc,
        new_include=include_keywords,
        new_exclude=exclude_keywords,
        author=author,
        source_archive_rowid=source_archive_rowid,
        created_at=now,
    )
    _record_guard_snapshot(
        conn,
        tag=tag,
        include_keywords=include_keywords,
        exclude_keywords=exclude_keywords,
        author=author,
        source_archive_rowid=source_archive_rowid,
        created_at=now,
    )


def _record_guard_events(
    conn: sqlite3.Connection,
    *,
    tag: str,
    prev_include: str,
    prev_exclude: str,
    new_include: str,
    new_exclude: str,
    author: str,
    source_archive_rowid: int | None,
    created_at: str,
) -> None:
    prev_i = {t.lower(): t for t in _split_csv_keywords(prev_include)}
    prev_e = {t.lower(): t for t in _split_csv_keywords(prev_exclude)}
    new_i = {t.lower(): t for t in _split_csv_keywords(new_include)}
    new_e = {t.lower(): t for t in _split_csv_keywords(new_exclude)}
    for key in sorted(new_i.keys() - prev_i.keys()):
        conn.execute(
            f"""
            INSERT INTO {TABLE_TAG_GUARD_EVENTS}
                (tag, action, token, author, source_archive_rowid, created_at)
            VALUES (?, 'add_include', ?, ?, ?, ?)
            """,
            (tag, new_i[key], author, source_archive_rowid, created_at),
        )
    for key in sorted(prev_i.keys() - new_i.keys()):
        conn.execute(
            f"""
            INSERT INTO {TABLE_TAG_GUARD_EVENTS}
                (tag, action, token, author, source_archive_rowid, created_at)
            VALUES (?, 'remove_include', ?, ?, ?, ?)
            """,
            (tag, prev_i[key], author, source_archive_rowid, created_at),
        )
    for key in sorted(new_e.keys() - prev_e.keys()):
        conn.execute(
            f"""
            INSERT INTO {TABLE_TAG_GUARD_EVENTS}
                (tag, action, token, author, source_archive_rowid, created_at)
            VALUES (?, 'add_exclude', ?, ?, ?, ?)
            """,
            (tag, new_e[key], author, source_archive_rowid, created_at),
        )
    for key in sorted(prev_e.keys() - new_e.keys()):
        conn.execute(
            f"""
            INSERT INTO {TABLE_TAG_GUARD_EVENTS}
                (tag, action, token, author, source_archive_rowid, created_at)
            VALUES (?, 'remove_exclude', ?, ?, ?, ?)
            """,
            (tag, prev_e[key], author, source_archive_rowid, created_at),
        )


def _record_guard_snapshot(
    conn: sqlite3.Connection,
    *,
    tag: str,
    include_keywords: str,
    exclude_keywords: str,
    author: str,
    source_archive_rowid: int | None,
    created_at: str,
) -> None:
    row = conn.execute(
        f"SELECT COALESCE(MAX(version), 0) FROM {TABLE_TAG_GUARD_SNAPSHOTS} WHERE tag = ?",
        (tag,),
    ).fetchone()
    next_version = int(row[0] or 0) + 1
    conn.execute(
        f"""
        INSERT INTO {TABLE_TAG_GUARD_SNAPSHOTS}
            (tag, version, include_keywords, exclude_keywords, author, source_archive_rowid, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tag,
            next_version,
            include_keywords,
            exclude_keywords,
            author,
            source_archive_rowid,
            created_at,
        ),
    )


def _apply_tag_guards(
    text: str,
    attrs: list[str],
    weights: dict[str, float],
    guards: dict[str, tuple[str, str]],
    include_signal: dict[str, float] | None = None,
) -> tuple[list[str], dict[str, float]]:
    text_l = (text or "").lower()
    active = {tag_catalog.normalize_tag(a) for a in attrs if tag_catalog.normalize_tag(a)}
    w = {
        tag_catalog.normalize_tag(k): float(v)
        for k, v in (weights or {}).items()
        if tag_catalog.normalize_tag(str(k))
    }
    known_tags = set(guards.keys()) | set(active) | {"unknown"}
    for tag in sorted(known_tags):
        include_csv, exclude_csv = guards.get(tag, ("", ""))
        inc = [t.lower() for t in _split_csv_keywords(include_csv)]
        exc = [t.lower() for t in _split_csv_keywords(exclude_csv)]
        has_inc = bool(inc) and any(t in text_l for t in inc)
        has_exc = bool(exc) and any(t in text_l for t in exc)
        if has_exc and tag in active:
            active.remove(tag)
            if tag in w:
                w[tag] = min(w.get(tag, 1.0), 0.01)
        # Include guards can add tags (except UNKNOWN, which remains a fallback state).
        if has_inc and tag != "unknown":
            active.add(tag)
            signal = 1.0
            if include_signal is not None:
                signal = max(0.0, min(2.0, float(include_signal.get(tag, 1.0))))
            boosted = min(INCLUDE_MAX, INCLUDE_BOOST_BASE + INCLUDE_BOOST_MULT * signal)
            w[tag] = max(w.get(tag, 0.0), boosted)
    if "unknown" in active:
        return ["unknown"], {"unknown": max(w.get("unknown", 1.0), 1.0)}
    if not active:
        return ["unknown"], {"unknown": 1.0}
    ordered = sorted(active)
    for t in ordered:
        w.setdefault(t, 1.0)
    return ordered, w


def merge_llm_and_human_keyword_hints(base: str, human: str) -> str:
    """
    Combine heuristic / model-side keyword hints with human-entered hints for the LLM column.
    Order: base tokens first, then human-only tokens; case-insensitive dedupe.
    """
    chunks: list[str] = []
    seen: set[str] = set()
    for part in (base or "").split(",") + (human or "").split(","):
        t = part.strip()
        if not t:
            continue
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        chunks.append(t)
    return ", ".join(chunks)


def strip_human_tokens_already_in_merged_llm(human: str, merged_llm: str) -> str:
    """
    For UI: drop human comma-separated tokens that already appear in the LLM hints column
    (heuristic + merged human). After Apply, those tokens show in the LLM row only.
    Comparison is per-token, case-insensitive.
    """
    if not (human or "").strip():
        return ""
    merged_parts = {p.strip().lower() for p in (merged_llm or "").split(",") if p.strip()}
    kept: list[str] = []
    for part in (human or "").split(","):
        t = part.strip()
        if not t:
            continue
        if t.lower() in merged_parts:
            continue
        kept.append(t)
    return ", ".join(kept)


def _fetch_training_row(
    conn: sqlite3.Connection, archive_rowid: int, tag: str
) -> tuple[int, str | None, int | None, str | None] | None:
    row = conn.execute(
        f"SELECT llm_checked, llm_keywords, human_checked, human_keywords "
        f"FROM {TABLE_TRAINING} WHERE archive_rowid = ? AND tag = ?",
        (archive_rowid, tag),
    ).fetchone()
    if not row:
        return None
    return row[0], row[1], row[2], row[3]


def build_message_state(
    conn: sqlite3.Connection,
    *,
    archive_rowid: int,
    text: str,
    subject: str | None,
    handle: str | None,
    date_ns: int | None,
    classifier_attributes_raw: object | None,
    generated_at_iso: str,
) -> dict[str, object]:
    """JSON-serializable payload for the training UI.

    When there is no per-tag training row yet and archived subject+body are both empty: ``education`` /
    ``spam`` from classifier_attributes (rich-only heuristic) are not shown as selected; only ``unknown`` has
    its LLM checkbox on and Human off, matching the index UNKNOWN filter.
    """
    text = coerce_str_field(text)
    subject_s = coerce_str_field(subject)
    handle_s = coerce_str_field(handle)
    date_ns = coerce_apple_timestamp_ns(date_ns)
    ensure_training_tables(conn)
    training_tags = _training_tags(conn)
    guards = _fetch_tag_guards(conn)
    include_signal = _fetch_tag_include_signal(conn, now_iso=utc_now_iso())
    attrs_list, weights_map = classifier.decode_classifier_blob(classifier_attributes_raw)
    attrs = set(attrs_list)
    no_plaintext = not text.strip() and not subject_s.strip()
    tags_out: list[dict[str, object]] = []

    for tag in training_tags:
        tr = _fetch_training_row(conn, archive_rowid, tag)
        in_llm = tag in attrs
        llm_w = weights_map.get(tag)
        if no_plaintext:
            # For truly empty subject+body rows, treat UNKNOWN as the only meaningful model
            # score in the UI even if legacy classifier_attributes still carries stale tags.
            llm_w = 1.0 if tag == "unknown" else None
        if tr is None:
            if no_plaintext:
                if tag == "unknown":
                    llm_checked = True
                    human_checked = False
                else:
                    llm_checked = False
                    human_checked = False
                human_keywords = ""
            else:
                llm_checked = in_llm
                human_checked = llm_checked
                human_keywords = ""
        else:
            llm_c, _llm_kw_db, hum_c, hum_kw = tr
            llm_checked = bool(llm_c)
            human_checked = llm_checked if hum_c is None else bool(hum_c)
            human_keywords = coerce_str_field(hum_kw) if hum_kw is not None else ""

        base_kw = _keywords_for_tag(tag, text)
        llm_keywords = merge_llm_and_human_keyword_hints(base_kw, human_keywords)
        human_keywords_display = strip_human_tokens_already_in_merged_llm(
            human_keywords, llm_keywords
        )
        # ``education`` is the only tag with body heuristics; if the model tagged it but no marker
        # matched, explain instead of a blank LLM hint row.
        if (
            not (llm_keywords or "").strip()
            and tag == "education"
            and in_llm
            and llm_checked
        ):
            llm_keywords = "[no heuristic keyword match in body]"

        tags_out.append(
            {
                "tag": tag,
                "llm_checked": llm_checked,
                "llm_weight": llm_w,
                "llm_keywords": llm_keywords,
                "human_checked": human_checked,
                "human_keywords": human_keywords_display,
                "model_include_guards": guards.get(tag, ("", ""))[0],
                "model_exclude_guards": guards.get(tag, ("", ""))[1],
            }
        )

    meta = conn.execute(
        f"SELECT last_llm_reason, last_training_regenerate_at FROM {TABLE_META} "
        f"WHERE archive_rowid = ?",
        (archive_rowid,),
    ).fetchone()
    last_reason = coerce_str_field(meta[0]) if meta else ""
    last_training_at = coerce_str_field(meta[1]) if meta and len(meta) > 1 else ""

    return {
        "rowid": archive_rowid,
        "text": text,
        "subject": subject_s,
        "handle": handle_s,
        "date_ns": date_ns,
        "classifier_attributes": sorted(attrs),
        "classifier_weights": weights_map,
        "generated_at_iso": generated_at_iso,
        "last_llm_reason": last_reason or "",
        "last_training_regenerate_at": last_training_at or "",
        "tags": tags_out,
    }


def _guidance_from_human_rows(rows: list[dict[str, object]]) -> str:
    lines: list[str] = []
    for r in rows:
        tag = tag_catalog.normalize_tag(str(r.get("tag", "")))
        if not tag:
            continue
        checked = r.get("human_checked")
        kw = str(r.get("human_keywords", "") or "").strip()
        c = "yes" if checked else "no"
        lines.append(
            f"- {tag}: human says this tag applies={c}; matching hints: {kw or '(none)'}"
        )
    return "\n".join(lines)


def apply_regenerate(
    conn: sqlite3.Connection,
    *,
    archive_rowid: int,
    message_text: str,
    message_subject: str | None = None,
    human_tag_rows: list[dict[str, object]],
    author: str = "unknown",
) -> tuple[list[str], str]:
    """
    Persist human_* from payload, call classifier with guidance, update archive + LLM snapshot.
    Returns (new_attributes, reason).
    """
    ensure_training_tables(conn)
    training_tags = _training_tags(conn)
    human_by_tag: dict[str, tuple[int, str, str, str, bool]] = {}
    for r in human_tag_rows:
        tag = tag_catalog.normalize_tag(str(r.get("tag", "")))
        if not tag or tag not in training_tags:
            continue
        hum_c = 1 if r.get("human_checked") else 0
        hum_kw = str(r.get("human_keywords", "") or "")
        has_explicit_guard_fields = (
            "human_include_guards" in r or "human_exclude_guards" in r
        )
        hum_inc = str(r.get("human_include_guards", "") or "")
        hum_exc = str(r.get("human_exclude_guards", "") or "")
        human_by_tag[tag] = (
            hum_c,
            hum_kw,
            hum_inc,
            hum_exc,
            has_explicit_guard_fields,
        )

    # Persist per-tag model guards inferred from human hints before classification output
    # is finalized, so guard updates can affect this regenerate immediately.
    guards = _fetch_tag_guards(conn)
    for tag, (hum_c, hum_kw, hum_inc, hum_exc, has_explicit_guard_fields) in human_by_tag.items():
        inc_existing, exc_existing = guards.get(tag, ("", ""))
        if has_explicit_guard_fields:
            inc_new = _merge_keyword_tokens(hum_inc)
            exc_new = _merge_keyword_tokens(hum_exc)
            inc_next = _merge_keyword_tokens(inc_existing, inc_new)
            exc_next = _merge_keyword_tokens(exc_existing, exc_new)
        else:
            inc_new, exc_new = _parse_human_keywords_guards(hum_kw)
            if hum_c:
                inc_next = _merge_keyword_tokens(inc_existing, inc_new)
                exc_next = _merge_keyword_tokens(exc_existing, exc_new)
            else:
                # Legacy behavior for older clients without explicit guard fields:
                # unchecked + plain tokens become veto guards.
                veto_from_plain = _merge_keyword_tokens(inc_new, exc_new)
                inc_next = _merge_keyword_tokens(inc_existing)
                exc_next = _merge_keyword_tokens(exc_existing, veto_from_plain)
        _upsert_tag_guard(
            conn,
            tag=tag,
            include_keywords=inc_next,
            exclude_keywords=exc_next,
            author=(author or "unknown").strip() or "unknown",
            source_archive_rowid=archive_rowid,
        )
    guards = _fetch_tag_guards(conn)
    include_signal = _fetch_tag_include_signal(conn, now_iso=utc_now_iso())

    # Empty subject+body: force deterministic UNKNOWN (100%) and skip model call.
    if not (coerce_str_field(message_text).strip() or coerce_str_field(message_subject).strip()):
        attrs = ["unknown"]
        weights = {"unknown": 1.0}
        reason = "no subject or body — forced unknown (training regenerate)"
    else:
        guidance = _guidance_from_human_rows(human_tag_rows)
        res = classifier.classify_message(
            message_text,
            human_guidance=guidance if guidance.strip() else None,
        )
        attrs, weights = _apply_tag_guards(
            message_text, res.attributes, res.weights, guards, include_signal=include_signal
        )
        reason = res.reason

    new_set = set(attrs)
    for tag in training_tags:
        hum_c, hum_kw, _inc, _exc, _has_explicit = human_by_tag.get(
            tag, (0, "", "", "", False)
        )
        llm_c = 1 if tag in new_set else 0
        llm_kw = merge_llm_and_human_keyword_hints(
            _keywords_for_tag(tag, message_text), hum_kw
        )
        conn.execute(
            f"""
            INSERT INTO {TABLE_TRAINING}
                (archive_rowid, tag, llm_checked, llm_keywords, human_checked, human_keywords)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(archive_rowid, tag) DO UPDATE SET
                llm_checked = excluded.llm_checked,
                llm_keywords = excluded.llm_keywords,
                human_checked = excluded.human_checked,
                human_keywords = excluded.human_keywords
            """,
            (archive_rowid, tag, llm_c, llm_kw, hum_c, hum_kw),
        )

    archive_mod._write_classifier_attributes(
        conn, _archive_table_for_conn(conn), archive_rowid, attrs, weights
    )
    retrained_at = utc_now_iso()
    conn.execute(
        f"""
        INSERT INTO {TABLE_META}
            (archive_rowid, last_llm_reason, last_training_regenerate_at)
        VALUES (?, ?, ?)
        ON CONFLICT(archive_rowid) DO UPDATE SET
            last_llm_reason = excluded.last_llm_reason,
            last_training_regenerate_at = excluded.last_training_regenerate_at
        """,
        (archive_rowid, reason, retrained_at),
    )
    conn.commit()
    return attrs, reason


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z"

