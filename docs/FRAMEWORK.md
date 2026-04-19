# High-level framework

This document describes how the pieces fit together: data flow, **tag catalog**, **policies**, **classification** (including tag weights and JSON persistence), **archive**, **training**, and **future work**. For setup and permissions see [SETUP.md](SETUP.md).

---

## 1. Data plane

- **One primary database:** Apple’s **`chat.db`** (typically `~/Library/Messages/chat.db`, overridable via `.env`).
- **Apple tables** (`message`, `chat`, `chat_message_join`, `handle`, …) are read for live traffic and updated when the agent archives, purges, or marks read.
- **sms-ripper tables** live in the same file: tag catalog, archive copy, training metadata, and model guards (all names prefixed with `sms_ripper_` or the canonical archive name below).

---

## 2. End-to-end pipeline

```text
chat.db (live messages)
    → reader.Message
    → classifier (Claude + optional keyword heuristics)
    → rules.evaluate (chosen policy)
    → actions (archive / purge / block / …)
```

1. **`reader.py`** — Loads recent inbound messages as **`Message`** rows (text, handle, dates, etc.).
2. **`classifier.py`** — Builds the system prompt from **active catalog tags**, calls the API when appropriate, normalizes all tag keys to **lowercase**, applies **keyword heuristics** and **unknown exclusivity**, then applies **`TAG_WEIGHT_THRESHOLD`** when the model supplied weights.
3. **`rules.py`** — Selects a **policy** (`political` or `spam`): two different **ordered rule lists**, not two different databases. Each **Rule** matches on **`Message.attributes`** (normalized tags).
4. **`actions.py`** — Executes merged actions (with ordering guarantees, e.g. archive before delete).

**Dry run** runs the same classification and rule merge but does not perform destructive actions.

---

## 3. Tag catalog (vocabulary)

- **Table:** `sms_ripper_tag_catalog` (`tag_catalog.py`).
- **Meaning:** Tags are **rows you define** (lowercase keys), not a hardcoded enum in the classifier. The default seed is only a starting point; if you add, merge, or extend tags, keep **rules**, **archive routing**, and **keyword heuristics** aligned.
- **Canonical default seed:** `tag_catalog.DEFAULT_TAG_ROWS` (**10** rows as shipped). In the seed, **`education`**, **`church`**, and **`sofi`** are archive-enabled; other keys are classification-only unless you change flags in the catalog UI.
- **Active tags** drive the LLM prompt (“available attributes”). **`archive_enabled`** (per row) participates in choosing archival destinations where applicable.
- **Tag catalog — merge only:** **Merge into** folds tag **A** into an **existing** tag **B** (`merge_classifier_tag_into` / training-server API `op: "merge"`): rewrites JSON on every archive table that has `classifier_attributes`, merges training and guards, then **deletes** **A** from the catalog. To switch to a **new** key, **add** **B** first, then merge **A** → **B**.

**Convention:** Stored and modeled tag strings are **lowercase** (e.g. `education`, `unknown`). In prose, **UNKNOWN** may be spelled in capitals when emphasizing the semantic role (“insufficient signal”), but JSON and SQL still use `unknown`.

Classifier attribute names come from **active catalog rows**, not a hardcoded enum in the LLM layer. Names like `education`, `spam`, or hypothetical `religion` are **examples**—whatever keys you seed or add (lowercase) are what the model uses and what `rules.py` matches.

---

## 4. Policies vs tags

- **`political` / `spam`** (CLI `--policy`, `main.py`, daemon) are **policy identifiers**: which **rule list** runs. They are **not** tag catalog keys.
- A **Rule**’s `name` field (e.g. `"political"`) is an internal label for logging/tests; it is also not a tag.
- **Tags** (`education`, `spam`, …) are what the classifier outputs and what conditions check.

---

## 5. Classification and tag weighting

### 5.1 Multi-label vs multiclass

The agent treats each message as **multi-label** classification: **several tags can apply at once** (e.g. both `education` and `spam`). Each tag has its own score in **[0, 1]**, interpreted as **confidence that the tag applies**.

This is **not** mutually exclusive **multiclass** classification (pick exactly one of *K* labels with scores that sum to 1). Here, scores are **independent per label**; multiple tags can all be **1.0** at the same time. That pattern is sometimes called **binary relevance** (one score per label).

At a high level, the classifier path also enforces:

- **`unknown` exclusivity:** If `unknown` is present in the model output, other tags are dropped for that message.
- **Keyword heuristics:** After the model returns, optional **`(tag, checker)`** pairs in **`KEYWORD_HEURISTIC_CHECKERS`** can add a tag when text matches. If the model **already** listed that tag, the heuristic does not change weights. Bounds come from **`keyword_heuristic_weight_bounds`** (defaults + per-tag overrides in `classifier.py`).

### 5.2 Changing catalog keys (merge, not rename)

To **replace** an old key **A** with a new key **B**, add **B** in the training server **Tag catalog** page, then use **Merge into** so **A** folds into **B** (rewrites stored JSON and training metadata; **A** is removed from the catalog). There is **no** in-place rename API. **`classifier.merge_tag_in_classifier_blob`** rewrites both list- and dict-shaped **`classifier_attributes`** values.

### 5.3 Model output (`classifier.py`)

Claude returns JSON with:

| Field | Role |
|--------|------|
| `attributes` | Tag names the model assigns (subset of the allowed vocabulary). |
| `reason` | Short explanation (logged and stored in training meta). |
| `weights` | Optional object: **same keys as the tags being scored**, values in **[0, 1]**. |

If `weights` is **omitted** or empty, behavior matches the older contract: every listed attribute is treated as fully on (**implicit weight 1.0** for each).

### 5.4 Which tags drive rules and archiving

Constants in `classifier.py` (not `.env`):

- **`TAG_WEIGHT_THRESHOLD`** (default **0.5**): when the model sends a **non-empty** `weights` object, only tags with **weight ≥ threshold** are kept in the **`attributes` list** passed to **`rules.py`** and **`Message.attributes`**. Tags below the threshold may still appear in the stored **`weights`** map for inspection and the training UI.
- If every tag would be filtered out (all below threshold), the pipeline **falls back** to the full model `attributes` list so the message is not left with no tags by accident.

### 5.5 Keyword heuristics (any catalog tag)

`classifier.py` can merge extra tags after the model returns when text matches registered checkers (`KEYWORD_HEURISTIC_CHECKERS`: pairs of tag key and `text -> bool`). If the model **already** listed that tag, the merge **skips** it (no weight change).

Weight for a heuristic-added tag is clamped to **[lo, hi]** using `keyword_heuristic_weight_bounds(tag)`:

- **`DEFAULT_KEYWORD_HEURISTIC_MIN_WEIGHT`** / **`DEFAULT_KEYWORD_HEURISTIC_MAX_WEIGHT`** (defaults **0.88** / **1.0**).
- **`KEYWORD_HEURISTIC_MIN_WEIGHT_BY_TAG`** / **`KEYWORD_HEURISTIC_MAX_WEIGHT_BY_TAG`** — optional per-tag overrides (normalized lowercase keys).

The shipped setup registers civic/PAC-style markers under the default tag **`education`** (`CIVIC_EDUCATION_KEYWORD_*`). Add another tag by defining markers plus a checker, appending to `KEYWORD_HEURISTIC_CHECKERS`, and setting bounds if you do not want the defaults.

### 5.6 Persistence (`message_tags_archive.classifier_attributes`)

Stored as JSON in either form:

1. **Legacy:** `["education", "spam"]` (example keys) — interpreted as weight **1.0** for each listed tag when read.
2. **Current:** `{"attributes": [...], "weights": {"education": 0.92, "spam": 0.88, ...}}` — **`attributes`** drive rules at write time; **`weights`** holds per-tag scores (including tags that scored below threshold, when the model supplied weights).

Helpers: `classifier.encode_classifier_blob`, `classifier.decode_classifier_blob`.

### 5.7 Training UI (column **W**)

On **`/message/<rowid>`**, column **W** shows the LLM **weight** (two decimal places) for each tag row when present. The API payload also includes **`classifier_weights`** for the full map.

### 5.8 Related scripts

- **`main.py`** — sets `Message.attributes` and `Message.attribute_weights` from `ClassificationResult`.
- **`scripts/dry_run_recent.py`** — full mode can print a **Weights:** line per message.
- **`scripts/reclassify_archive_tags.py`** — refreshes archive rows using the same classifier and JSON shape.

See [QUERIES.md](QUERIES.md) for preview commands that hit the classifier.

---

## 6. Archive

- **Canonical table:** **`message_tags_archive`** — copy of `message` shape plus sms-ripper columns (`classifier_attributes`, optional daemon cycle metadata).
- **Fail-fast:** Tools that need the archive (**report generator**, **training server**, **review**, **reclassify** when using the default table) call **`archive.require_archive_table`**. If the table is **missing**, they **raise** immediately—there is **no** fallback to alternate historical table names or automatic rename from legacy schemas.
- **Creation:** The first successful **`archive_message`** path runs **`_ensure_archive_table`**, which **creates** `message_tags_archive` from the `message` schema if it does not exist (Messages must be quit for writes).

---

## 7. Human-in-the-loop training

- **HTTP UI:** `scripts/archive_training_server.py` (e.g. `poe archive-training-server`) — loopback-only server over **`message_tags_archive`**.
- **Tag catalog page:** **`GET /tag-catalog`** — add or archive catalog rows; **Merge into** only for changing keys (add the target tag first, then merge source → target). API: **`POST /api/tag-catalog`** with **`op: "merge"`** and **`from`** / **`into`** (see §3).
- **Extra tables:** `sms_ripper_archive_tag_training`, `sms_ripper_archive_training_meta`, guard/snapshot/trust tables — created by `archive_tag_training.py` on demand.
- **Apply** re-runs the classifier with human hints and updates **`classifier_attributes`** plus training rows.

---

## 8. Where to look in code

| Concern | Module / entry |
|--------|-----------------|
| Tag vocabulary & seed | `tag_catalog.py` |
| LLM + heuristics + weights | `classifier.py` |
| Policy & rules | `rules.py` |
| Side effects | `actions.py` |
| Archive copy & purge | `archive.py` |
| Training DB + regenerate | `archive_tag_training.py` |
| Agent loop | `main.py` |

---

## 9. Related docs

- [QUERIES.md](QUERIES.md) — Read-only previews and SQL helpers.
- [DAEMON.md](DAEMON.md) — Scheduled runs and logs.
- [TESTING.md](TESTING.md) — pytest and `poe` tasks.

---

## 10. Future work (estimates)

Rough engineering-time notes for planning. Not commitments; actual time depends on reviewer feedback, real `chat.db` edge cases, and scope creep.

### Classifier tags on archived rows (`classifier_attributes`)

**Status:** **Shipped.** Archive tables carry **`classifier_attributes`** (JSON) set at archive time from `Message.attributes`; see `archive.py`, §5–6 above.

**Still worth planning separately**

- **Large-scale backfill** of historical rows (batch API, rate limits, idempotency) — much larger than the column add; use `poe reclassify-archive-tags` / `scripts/reclassify_archive_tags.py` for controlled batches (`--dry-run`, `--delay`, `--limit`).
- **Review weak coverage:** `poe review-untagged-archive` (optional `--suggest`, `--include-unknown`, CSV export).

### Report UI: filter archive rows

**Status:** **Largely shipped.** `scripts/generate_report_html.py` generates **`reports/index.html`** with an **Archive type** `<select>`, client-side row filtering via `data-archive-types`, and cookie persistence for the selection (see [CHANGELOG.md](../CHANGELOG.md) and the script’s `archive-type-filter` / cookie helpers).

**Possible follow-ups (if product needs them)**

- UX or labeling tweaks when **`classifier_attributes`** encodes edge cases (e.g. empty plaintext + legacy JSON).
- Any **additional** views not covered by the current archive-type column (e.g. dashboards that aggregate across tables) — scope-dependent.

Rough **incremental** effort for a narrow UI-only tweak: **~1–3 hours**; anything involving new data pipelines should be estimated with the relevant scripts and tests.

### Earlier scoping note (multi-tag + filter)

- **Classifier** already returns multiple `attributes` on `Message`; **persistence** on `message_tags_archive` is in place.
- **Static report** exposes types derived from stored tags for filtering; regenerate after daemon runs or `poe report-generate`.

*Revise §10 as implementation details change.*
