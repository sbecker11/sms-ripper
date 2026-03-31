"""
SQLite tables + helpers for human-in-the-loop tag training on POLITICAL_archive rows.

Used by ``scripts/archive_training_server.py``. Tables are created on first server use.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Final

import archive as archive_mod
import classifier

TABLE_TRAINING = "sms_ripper_archive_tag_training"
TABLE_META = "sms_ripper_archive_training_meta"
ARCHIVE_TABLE = "POLITICAL_archive"

# All classifier attributes we show as rows in the training UI.
TRAINING_TAGS: Final[tuple[str, ...]] = (
    "POLITICAL",
    "SPAM",
    "STOP",
    "SCAM",
    "PROMO",
    "LEGIT",
    "PERSONAL",
    "UNKNOWN",
)


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


def ensure_training_tables(conn: sqlite3.Connection) -> None:
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


def _ensure_meta_last_training_regenerate_column(conn: sqlite3.Connection) -> None:
    names = {r[1] for r in conn.execute(f"PRAGMA table_info({TABLE_META})").fetchall()}
    if "last_training_regenerate_at" not in names:
        conn.execute(
            f"ALTER TABLE {TABLE_META} ADD COLUMN last_training_regenerate_at TEXT"
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
        for phrase in classifier.POLITICAL_TEXT_MARKERS:
            if phrase in raw_l or phrase in norm:
                hits.append(phrase)
        for pat in classifier.POLITICAL_TEXT_PATTERNS:
            m = pat.search(text) or pat.search(norm)
            if m and m.group(0) not in hits:
                hits.append(m.group(0))
        return ", ".join(hits[:12])
    except Exception:
        return ""


def _keywords_for_tag(tag: str, text: str) -> str:
    if tag == "POLITICAL":
        return heuristic_keywords_political(text)
    return ""


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

    When there is no per-tag training row yet and archived subject+body are both empty: POLITICAL /
    SPAM from classifier_attributes (rich-only heuristic) are not shown as selected; only UNKNOWN has
    its LLM checkbox on and Human off, matching the index UNKNOWN filter.
    """
    text = coerce_str_field(text)
    subject_s = coerce_str_field(subject)
    handle_s = coerce_str_field(handle)
    date_ns = coerce_apple_timestamp_ns(date_ns)
    ensure_training_tables(conn)
    attrs_list, weights_map = classifier.decode_classifier_blob(classifier_attributes_raw)
    attrs = set(attrs_list)
    no_plaintext = not text.strip() and not subject_s.strip()
    tags_out: list[dict[str, object]] = []

    for tag in TRAINING_TAGS:
        tr = _fetch_training_row(conn, archive_rowid, tag)
        in_llm = tag in attrs
        llm_w = weights_map.get(tag)
        if tr is None:
            if no_plaintext:
                if tag == "UNKNOWN":
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
        # POLITICAL is the only tag with body heuristics; if the model tagged it but no marker
        # matched, explain instead of a blank LLM hint row.
        if (
            not (llm_keywords or "").strip()
            and tag == "POLITICAL"
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
        tag = str(r.get("tag", "")).strip().upper()
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
    human_tag_rows: list[dict[str, object]],
) -> tuple[list[str], str]:
    """
    Persist human_* from payload, call classifier with guidance, update archive + LLM snapshot.
    Returns (new_attributes, reason).
    """
    ensure_training_tables(conn)
    guidance = _guidance_from_human_rows(human_tag_rows)
    res = classifier.classify_message(
        message_text,
        human_guidance=guidance if guidance.strip() else None,
    )
    attrs = res.attributes
    reason = res.reason
    weights = res.weights

    human_by_tag: dict[str, tuple[int, str]] = {}
    for r in human_tag_rows:
        tag = str(r.get("tag", "")).strip().upper()
        if tag not in TRAINING_TAGS:
            continue
        hum_c = 1 if r.get("human_checked") else 0
        hum_kw = str(r.get("human_keywords", "") or "")
        human_by_tag[tag] = (hum_c, hum_kw)

    new_set = set(attrs)
    for tag in TRAINING_TAGS:
        hum_c, hum_kw = human_by_tag.get(tag, (0, ""))
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
        conn, ARCHIVE_TABLE, archive_rowid, attrs, weights
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
