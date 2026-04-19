# Changelog

All notable changes to this project are documented here. Entries use **UTC** timestamps (`YYYY-MM-DDThh:mm:ssZ UTC`). **Newest first.**

---

## 2026-04-18T22:00:00Z UTC

- **Docs:** Added **`docs/README.md`** as a short index of all **`docs/*.md`** files; **`README.md`** Setup section links to it.

---

## 2026-04-18T20:00:00Z UTC

- **Docs:** Merged **`docs/CLASSIFICATION.md`** and **`FUTURE_WORK.md`** into **`docs/FRAMEWORK.md`** (§5 *Classification and tag weighting*; §10 *Future work*). Removed the standalone files; updated **`README.md`**, **`docs/QUERIES.md`**, and **`docs/SETUP.md`** to link to the new sections.

---

## 2026-04-18T12:00:00Z UTC

- **Docs:** Documented the default tag seed as **`tag_catalog.DEFAULT_TAG_ROWS`** (**10** tags) in **`docs/QUERIES.md`**, **`docs/FRAMEWORK.md`**, and **`docs/CLASSIFICATION.md`**; **`README.md`** classifier bullet points to that tuple as the single source of truth. **`tag_catalog.py`** module docstring notes the shipped count.

---

## 2026-04-01T18:00:00Z UTC

- **Civic keyword heuristic:** Added **fundgop.net** and **us4u.io** to ``education`` merge markers and domain regex so SMS with those hosts get the political archive path (``message_tags_archive`` under default ``political`` policy). Broadened **GOP** matching: ``txt gop`` / ``text gop`` phrases, **G.O.P.** / spaced-letter forms, and ``g o p`` after punctuation normalization. **Ted Cruz:** regex ``\bted\s+cruz\b`` (handles double spaces), phrases **senator cruz** / **sen. cruz**, and ``\bsen(?:ator)?\.?\s+cruz\b``. **Josh Hawley:** same pattern — ``josh hawley``, **senator hawley** / **sen. hawley**, ``\bjosh\s+hawley\b``, ``\bsen(?:ator)?\.?\s+hawley\b``. **John Kennedy** (already had substring markers): **sen. kennedy**, ``\bjohn\s+kennedy\b``, ``\bsen(?:ator)?\.?\s+kennedy\b``. **John Thune:** ``john thune``, **senator thune** / **sen. thune**, ``\bjohn\s+thune\b``, ``\bsen(?:ator)?\.?\s+thune\b``. **Defund / defunded:** markers **defund** / **defunding** / **defunded** and regex ``\bdefund(?:ed|ing|s)?\b``. **Government:** marker and ``\bgovernment\b`` (broad — may match non-PAC SMS that mention government). **NYC radicals:** ``nyc radicals``, ``nyc radical``, ``\bnyc\s+radical(s)?\b``. **Congress:** standalone marker **congress** (plus existing *congress* phrases) and ``\bcongress\b`` (does not match inside *Congressional* as a whole word — substring marker still catches *Congressional*). **Oval Office:** ``oval office`` and ``\boval\s+office\b``.

---

## 2026-04-01T16:00:00Z UTC

- **Outbound STOP:** Recent **from-me** messages whose body/subject is only ``STOP_REPLY_TEXT`` from ``.env`` (default **STOP**, with optional ``.`` / ``!`` when that value is **STOP**) are loaded each run, **not** sent to the classifier, and match a new first rule on both policies: **delete** the **thread** in Messages (AppleScript). Inbound **STOP** tagging under **spam** policy is unchanged. Helpers: ``reader.get_recent_outbound_stop_replies``, ``reader.plain_text_is_user_stop_command``.

---

## 2026-04-01T14:00:00Z UTC

- **Tag `sofi`:** Default catalog adds **`sofi`** (archive-enabled) for SoFi-branded SMS (fraud / verify-spend, account alerts). **`rules.py`** (`political`) archives non-personal **`sofi`** into **`sofi_archive`**. **`classifier._sofi_keyword_hit`** + **`KEYWORD_HEURISTIC_CHECKERS`** add **`sofi`** when the body contains the word **SoFi** (so **`transactional`-only** model output still archives to **`sofi_archive`**). **`ARCHIVAL_TAG_PRIORITY`** is **`church`**, **`sofi`**, **`education`**.

---

## 2026-04-01T12:00:00Z UTC

- **Tag `church`:** Default catalog seed adds **`church`** (archive-enabled) for ward/congregation bulk (programs, musical numbers, meeting notes). **`rules.py`** (`political`) archives non-personal **`church`** into **`church_archive`**. **`archive.first_archival_tag`** prefers **`church`** over **`education`** when both apply so mixed tagging lands in the church table.

---

## 2026-04-01T02:00:00Z UTC

- **Tag catalog UI / API:** Removed **rename**; only **merge** remains for changing tag keys. Use **Add** for the target name, then **Merge into**. Removed ``rename_classifier_tag`` and ``tag_catalog.rename_catalog_key``.

---

## 2026-04-01T01:00:00Z UTC

- **Tag merge:** **`merge_classifier_tag_into`** (and training-server catalog API **`op: "merge"`** with **`from`** / **`into`**) merges source tag **A** into existing **B**: rewrites **`classifier_attributes`** on all tables that have that column, merges training rows and tag guards (snapshots/events), then deletes **A** from **`sms_ripper_tag_catalog`**. **`classifier.merge_tag_in_classifier_blob`** handles list and dict JSON shapes. **`tag_catalog.delete_catalog_tag`** supports removal after merge. Reserved **`unknown`** cannot be merged away.

---

## 2026-04-01T00:00:00Z UTC

- **Tags:** Dropped **`scam`** from the default catalog; **`spam`** is the single bucket for junk and phishing. **`rules.py`** (`spam` policy) still matches **legacy** `scam`-only attributes via **`legacy_scam_only`** (block/delete, no STOP) so old archived JSON keeps working.

---

## 2026-03-31T23:15:00Z UTC

- **Tags:** Removed **`legit`** from the default catalog and from **`rules.py`** (no dedicated “legitimate” rule). Use **`personal`**, **`transactional`**, or **`unknown`** as appropriate. Archive report quick-review no longer uses a **`legit`**-vs-bulk conflict heuristic.

---

## 2026-03-31T22:30:00Z UTC

- **Default tag catalog:** Reduced the seed to **eight** common SMS categories: **`education`** (still archive-enabled for the default political rule), **`personal`**, **`transactional`**, **`promo`**, **`social`**, **`spam`**, **`stop`**, **`unknown`** ( **`scam`** merged into **`spam`** for new classifications). Removed the long topic/Yahoo-style expansion; add finer tags via the catalog UI if you want them back.

---

## 2026-03-31T20:00:00Z UTC

- **Default tag catalog:** Seed **Yahoo Mail** inbox-style tags **`primary`**, **`offers`**, **`social`**, and **`newsletters`** (from [Yahoo Help SLN36712](https://help.yahoo.com/kb/SLN36712.html), New Yahoo Mail categories). **`promo`** stays for existing rules; scope is similar to Yahoo **Offers**.

---

## 2026-03-31T18:00:00Z UTC

- **Default tag catalog:** Seed **`politics`**, **`fashion`**, **`local_news`**, **`domestic_news`**, **`global_news`**, **`religion`**, **`sports`**, **`children`**, and **`food`** (active, not archive-enabled by default) alongside **`education`**; default physical archive stays **`education`** → **`message_tags_archive`** unless you enable archive for another tag in the catalog UI (separate **`<tag>_archive`** table). Quick-review heuristics treat **`legit`** combined with those topic tags (plus **`spam`**, **`scam`**, **`education`**) as ambiguous. (Replaces a single **`news`** seed with three geographic scopes.)

---

## 2026-03-31T12:00:00Z UTC

- **Canonical tag strings:** Classifier output, `classifier_attributes` JSON, archive report dropdown/options, and training UI now use **lowercase** tag keys (e.g. `unknown`, `spam`, `education`) consistently with the SQLite tag catalog. Legacy uppercase values in stored JSON are still accepted on read and normalized.
- **`archive.first_archival_tag`:** The configured archival tag set is **normalized** the same way as message attributes, so custom `ARCHIVAL_TAGS` (any casing) matches correctly.
- **Tests:** Fixtures and assertions updated for lowercase tags and current report HTML (`<option value="unknown">`, `data-archive-types="unknown"` for empty plaintext rows).
- **README:** Top **objective** paragraph rewritten for the local macOS pipeline (catalog, rules, `message_tags_archive`, training UI, default civic/education focus).

---

## 2026-03-30T23:45:00Z UTC

- **Classifier tag semantics:** Enforced `UNKNOWN` exclusivity: if `UNKNOWN` is present, all other tags are dropped in `classifier.classify_message()` and `classifier.decode_classifier_blob()` (including legacy archived JSON).
- **Archive index filtering:** `generate_report_html.py` now forces `data-archive-types="UNKNOWN"` when both archived `subject` and `text` are empty (matches training UI), preventing `education` filter from matching truly empty rows even if legacy `classifier_attributes` still contains education/spam.

---

## 2026-03-30T23:00:00Z UTC

- **Changelog & index footer:** Added root **`CHANGELOG.md`** (UTC section headings `## …Z UTC`, newest first). The political archive **index** (`generate_report_html.py`, static and training server) includes a footer link to **`CHANGELOG.md`** plus **latest entry** text parsed from the top section heading (same string as after `## `). **`archive_training_server`** serves **`GET /CHANGELOG.md`**. README links to the changelog.
- **Training index UI:** Removed the long hint block from the bottom of the training-server index page (static `reports/index.html` hint unchanged).

---

## 2026-03-30T20:30:00Z UTC

- **Archive training UI (`build_message_state`):** For archived rows with **no non-empty subject or body** and **no** per-tag training row yet, the table now shows **only** the **UNKNOWN** tag with its **LLM** checkbox on and **Human** off. **`education`** / **`spam`** (and other tags) are **not** shown as selected, even when still present in `classifier_attributes` from the rich-only placeholder path at archive time. The `education` “no heuristic keyword match” hint only appears when that tag’s LLM row is actually selected.
- **Rationale:** Aligns the editor with the index **UNKNOWN** filter and avoids implying the human agreed to education/spam on empty plaintext.

---

## 2026-03-30T19:00:00Z UTC

- **Archive training UI:** When subject+body are empty and `classifier_attributes` omits UNKNOWN, **UNKNOWN** LLM was shown checked (index-filter parity); follow-up refined Human vs LLM (see entry above).
- **Political archive index (`generate_report_html.py`):** Selecting **UNKNOWN** in **Archive type** now includes rows with **empty stored `text`** (`data-archive-no-plaintext="1"`), not only rows tagged UNKNOWN in JSON—so “empty body” rows match the filter even if they still have education/spam in `classifier_attributes`.
- **Dropdown:** **UNKNOWN** is always listed in the archive-type `<select>` (not only when present in the current batch).

---

## 2026-03-30T16:45:00Z UTC

- **Report index copy:** Summary line shows **comma-formatted** total row count for `message_tags_archive` and path to the database; heading uses **“Latest \<N\> archived messages (newest first)”** from `--limit` / server `--limit`. **“Showing \<N\> row(s)”** after the filter kept for filtered count; filter row CSS adjusted for vertical alignment.
- **Archive type filter:** Selection persisted in cookie `smsRipperArchiveTypeFilter` (same general approach as timezone cookie: `path=/`, long `max-age`, `SameSite=Lax`).

---

## 2026-03-30T14:00:00Z UTC

- **`classifier.classify_message`:** If plaintext is empty or whitespace-only **and** there is no `human_guidance`, returns **`UNKNOWN`** immediately (no API call). Rich-only placeholder (`RICH_ONLY_PLACEHOLDER`) unchanged (education+spam heuristic). Guidance-only path still calls the API when text is empty.

---

## 2026-03-30T12:00:00Z UTC

- **Archive training server:** Training tag table columns reordered to **Tag, On, W, Source, Keywords / hints**; layout uses `<colgroup>` and checkbox scaling fix so columns do not overlap. **Apply** / **Done** behavior (regenerate + close vs close only) and related README hints as previously shipped.

---

*Earlier history was not tracked in this file; future changes can be appended above this line.*
