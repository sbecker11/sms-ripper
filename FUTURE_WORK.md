# Future work — estimates

Rough engineering-time notes for planning. Not commitments; actual time depends on reviewer feedback, real `chat.db` edge cases, and scope creep.

---

## Persist multiple classifier tags on archived messages (backend only)

**Status (2026-03):** Implemented in `archive.py` as column **`classifier_attributes`** (TEXT, JSON array). New and existing `*_archive` tables get the column via `ALTER TABLE` when needed. Rows archived before this change keep `NULL` until re-archived (unusual) or a future backfill.

**Backfill / re-tag archived rows:** run `python scripts/reclassify_archive_tags.py` (or `poe reclassify-archive-tags`) with `ANTHROPIC_API_KEY` in `.env`. Use `--dry-run` first, optional `--delay` for API rate limits, `--limit N` for a test batch.

**Review untagged / weakly tagged rows:** run `python scripts/review_untagged_archive.py` (or `poe review-untagged-archive`). Add `--include-unknown` to include `["UNKNOWN"]`-only rows; add `--suggest` to compare stored tags with the current classifier (needs API key). Use `--csv` / `--output file.csv` for spreadsheets.

**Goal:** Store one or more tags per archived row (e.g. JSON `["education","spam"]` in a new `message_tags_archive` column), populated from `Message.attributes` at archive time. Apple’s `message` table does not carry classifier tags; today they exist only in memory during a run.

**Typical implementation**

- Add an archive-only column (e.g. `classifier_attributes TEXT`).
- Ensure the column exists on existing DBs (`ALTER TABLE … ADD COLUMN` if missing), similar to `daemon_cycle_start` / `daemon_cycle_pid` in `archive.py`.
- On archive, after copying the live `message` row, set that column from `message.attributes`.
- Old rows: leave `NULL` or `[]` unless a separate backfill project is approved.
- Tests: `tests/test_archive.py` and any paths that assert archive shape.

**Effort (focused engineering time)**

| Context | Time |
|--------|------|
| Developer already comfortable with this repo | **~3–5 hours** |
| With surprises (locking, triggers, odd DB state) | **~1 day** |
| Add careful manual check on a real `chat.db` | **+1–2 hours** |

**Default ballpark:** **~half a day** focused backend work; **round up to a full day** with buffer for integration testing.

**Out of scope for the above numbers**

- **Backfill** historical rows via re-classification (batch API, rate limits, idempotency): **much larger**, separate project.
- Applying the same pattern to additional `*_archive` tables: **small incremental** cost per table.

**Likely touch points:** `archive.py`, `tests/test_archive.py`, optionally `docs/SETUP.md` or daemon docs if behavior is user-visible.

---

## Report UI: dropdown to filter rows by tag

**Goal:** Static `reports/index.html` with a `<select>` (e.g. “All”, “education”, “spam”, …) and client-side show/hide of table rows based on persisted tags.

**Dependency:** Tags must be **present in the generated HTML** (or embedded JSON), which requires **persistence** as above unless the report is regenerated from another source that still has attributes.

**Effort:** **~2–4 hours** on top of the backend work (template + small script + test assertions).

**Combined backend + filter UI:** **~1–2 days** calendar for a careful solo pass, or **~1 day** if everything goes smoothly.

---

## Earlier scoping note (multi-tag + filter, full picture)

- **Classifier already returns** multiple `attributes` on `Message` in Python; the gap is **persistence** on `message_tags_archive` for the static report.
- **UI-only filter** is easy **if** tags are already in the page data.
- **End-to-end “multiple tags + filter”** is **moderate** overall: one schema/write path in archive, then report/UI.

---

*Last updated from assistant estimates; revise as implementation details change.*
