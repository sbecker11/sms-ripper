# Changelog

All notable changes to this project are documented here. Entries use **UTC** timestamps (`YYYY-MM-DDThh:mm:ssZ UTC`). **Newest first.**

---

## 2026-03-30T23:45:00Z UTC

- **Classifier tag semantics:** Enforced `UNKNOWN` exclusivity: if `UNKNOWN` is present, all other tags are dropped in `classifier.classify_message()` and `classifier.decode_classifier_blob()` (including legacy archived JSON).
- **Archive index filtering:** `generate_report_html.py` now forces `data-archive-types="UNKNOWN"` when both archived `subject` and `text` are empty (matches training UI), preventing POLITICAL filter from matching truly empty rows even if legacy `classifier_attributes` still contains POLITICAL/SPAM.

---

## 2026-03-30T23:00:00Z UTC

- **Changelog & index footer:** Added root **`CHANGELOG.md`** (UTC section headings `## …Z UTC`, newest first). The political archive **index** (`generate_report_html.py`, static and training server) includes a footer link to **`CHANGELOG.md`** plus **latest entry** text parsed from the top section heading (same string as after `## `). **`archive_training_server`** serves **`GET /CHANGELOG.md`**. README links to the changelog.
- **Training index UI:** Removed the long hint block from the bottom of the training-server index page (static `reports/index.html` hint unchanged).

---

## 2026-03-30T20:30:00Z UTC

- **Archive training UI (`build_message_state`):** For archived rows with **no non-empty subject or body** and **no** per-tag training row yet, the table now shows **only** the **UNKNOWN** tag with its **LLM** checkbox on and **Human** off. **POLITICAL** / **SPAM** (and other tags) are **not** shown as selected, even when still present in `classifier_attributes` from the rich-only placeholder path at archive time. The POLITICAL “no heuristic keyword match” hint only appears when that tag’s LLM row is actually selected.
- **Rationale:** Aligns the editor with the index **UNKNOWN** filter and avoids implying the human agreed to POLITICAL/SPAM on empty plaintext.

---

## 2026-03-30T19:00:00Z UTC

- **Archive training UI:** When subject+body are empty and `classifier_attributes` omits UNKNOWN, **UNKNOWN** LLM was shown checked (index-filter parity); follow-up refined Human vs LLM (see entry above).
- **Political archive index (`generate_report_html.py`):** Selecting **UNKNOWN** in **Archive type** now includes rows with **empty stored `text`** (`data-archive-no-plaintext="1"`), not only rows tagged UNKNOWN in JSON—so “empty body” rows match the filter even if they still have POLITICAL/SPAM in `classifier_attributes`.
- **Dropdown:** **UNKNOWN** is always listed in the archive-type `<select>` (not only when present in the current batch).

---

## 2026-03-30T16:45:00Z UTC

- **Report index copy:** Summary line shows **comma-formatted** total row count for `POLITICAL_archive` and path to the database; heading uses **“Latest \<N\> archived messages (newest first)”** from `--limit` / server `--limit`. **“Showing \<N\> row(s)”** after the filter kept for filtered count; filter row CSS adjusted for vertical alignment.
- **Archive type filter:** Selection persisted in cookie `smsRipperArchiveTypeFilter` (same general approach as timezone cookie: `path=/`, long `max-age`, `SameSite=Lax`).

---

## 2026-03-30T14:00:00Z UTC

- **`classifier.classify_message`:** If plaintext is empty or whitespace-only **and** there is no `human_guidance`, returns **`UNKNOWN`** immediately (no API call). Rich-only placeholder (`RICH_ONLY_PLACEHOLDER`) unchanged (POLITICAL+SPAM heuristic). Guidance-only path still calls the API when text is empty.

---

## 2026-03-30T12:00:00Z UTC

- **Archive training server:** Training tag table columns reordered to **Tag, On, W, Source, Keywords / hints**; layout uses `<colgroup>` and checkbox scaling fix so columns do not overlap. **Apply** / **Done** behavior (regenerate + close vs close only) and related README hints as previously shipped.

---

*Earlier history was not tracked in this file; future changes can be appended above this line.*
