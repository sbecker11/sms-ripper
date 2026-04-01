# High-level framework

This document describes how the pieces fit together: data flow, **tag catalog**, **policies**, **classification**, **archive**, and **training**. For setup and permissions see [SETUP.md](SETUP.md). For tag JSON, weights, and heuristics detail see [CLASSIFICATION.md](CLASSIFICATION.md).

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
- **Active tags** drive the LLM prompt (“available attributes”). **`archive_enabled`** (per row) participates in choosing archival destinations where applicable.
- **Tag catalog — merge only:** **Merge into** folds tag **A** into an **existing** tag **B** (`merge_classifier_tag_into` / training-server API `op: "merge"`): rewrites JSON on every archive table that has `classifier_attributes`, merges training and guards, then **deletes** **A** from the catalog. To switch to a **new** key, **add** **B** first, then merge **A** → **B**.

**Convention:** Stored and modeled tag strings are **lowercase** (e.g. `education`, `unknown`). In prose, **UNKNOWN** may be spelled in capitals when emphasizing the semantic role (“insufficient signal”), but JSON and SQL still use `unknown`.

---

## 4. Policies vs tags

- **`political` / `spam`** (CLI `--policy`, `main.py`, daemon) are **policy identifiers**: which **rule list** runs. They are **not** tag catalog keys.
- A **Rule**’s `name` field (e.g. `"political"`) is an internal label for logging/tests; it is also not a tag.
- **Tags** (`education`, `spam`, …) are what the classifier outputs and what conditions check.

---

## 5. Classification model

- **Multi-label:** Several tags can apply; weights are **per tag**, not a single softmax winner.
- **`unknown`:** If present in the model output, other tags are dropped for that message (**unknown exclusivity**).
- **Keyword heuristics:** After the model returns, optional **`(tag, checker)`** pairs in **`KEYWORD_HEURISTIC_CHECKERS`** can add a tag when text matches. If the model **already** listed that tag, the heuristic does not change weights. Bounds come from **`keyword_heuristic_weight_bounds`** (defaults + per-tag overrides in `classifier.py`).
- **Persistence:** Archive column **`classifier_attributes`** holds JSON (legacy list or `attributes` + `weights` object); see [CLASSIFICATION.md](CLASSIFICATION.md).

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

- [CLASSIFICATION.md](CLASSIFICATION.md) — JSON shapes, thresholds, heuristic bounds, training column **W**.
- [QUERIES.md](QUERIES.md) — Read-only previews and SQL helpers.
- [DAEMON.md](DAEMON.md) — Scheduled runs and logs.
- [TESTING.md](TESTING.md) — pytest and `poe` tasks.
