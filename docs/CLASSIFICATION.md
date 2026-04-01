# Classification and tag weighting

For pipeline context (catalog, policies, archive table, training), see [FRAMEWORK.md](FRAMEWORK.md).

## Tag vocabulary (not fixed constants)

Classifier **attribute names** come from the **active rows** in `sms_ripper_tag_catalog` (`tag_catalog.py`), not from a hardcoded enum in the LLM layer. Names like `education`, `spam`, or hypothetical `religion` / `political` are **examples**—whatever keys you seed or add in the catalog (lowercase) are what the model is asked to use and what `rules.py` matches. This repo ships a **default seed**; align rules, archive routing, and optional keyword merges if you rename or extend tags.

## What kind of problem this is

The agent treats each message as **multi-label** classification: **several tags can apply at once** (e.g. both `education` and `spam` in the default setup). Each tag has its own score in **[0, 1]**, interpreted as **confidence that the tag applies**.

This is **not** mutually exclusive **multiclass** classification (pick exactly one of *K* labels with scores that sum to 1, e.g. softmax over a single winner). Here, scores are **independent per label**; multiple tags can all be **1.0** at the same time, so there is **no forced “relative maximum”** among tags unless you compare the stored weights yourself (e.g. `max(weights.values())`).

That independent-per-label pattern is sometimes called **binary relevance** (one yes/no—or score—per label).

## Model output (`classifier.py`)

Claude returns JSON with:

| Field | Role |
|--------|------|
| `attributes` | Tag names the model assigns (subset of the allowed vocabulary). |
| `reason` | Short explanation (logged and stored in training meta). |
| `weights` | Optional object: **same keys as the tags being scored**, values in **[0, 1]**. |

If `weights` is **omitted** or empty, behavior matches the older contract: every listed attribute is treated as fully on (**implicit weight 1.0** for each).

## Which tags drive rules and archiving

Constants in `classifier.py` (not `.env`):

- **`TAG_WEIGHT_THRESHOLD`** (default **0.5**): when the model sends a **non-empty** `weights` object, only tags with **weight ≥ threshold** are kept in the **`attributes` list** passed to **`rules.py`** and **`Message.attributes`**. Tags below the threshold may still appear in the stored **`weights`** map for inspection and the training UI.
- If every tag would be filtered out (all below threshold), the pipeline **falls back** to the full model `attributes` list so the message is not left with no tags by accident.

## Keyword heuristics (any catalog tag)

`classifier.py` can merge extra tags after the model returns when text matches registered checkers (`KEYWORD_HEURISTIC_CHECKERS`: pairs of tag key and `text -> bool`). If the model **already** listed that tag, the merge **skips** it (no weight change).

Weight for a heuristic-added tag is clamped to **[lo, hi]** using `keyword_heuristic_weight_bounds(tag)`:

- **`DEFAULT_KEYWORD_HEURISTIC_MIN_WEIGHT`** / **`DEFAULT_KEYWORD_HEURISTIC_MAX_WEIGHT`** (defaults **0.88** / **1.0**).
- **`KEYWORD_HEURISTIC_MIN_WEIGHT_BY_TAG`** / **`KEYWORD_HEURISTIC_MAX_WEIGHT_BY_TAG`** — optional per-tag overrides (normalized lowercase keys).

The shipped setup registers civic/PAC-style markers under the default tag **`education`** (`CIVIC_EDUCATION_KEYWORD_*`). Add another tag by defining markers plus a checker, appending to `KEYWORD_HEURISTIC_CHECKERS`, and setting bounds if you do not want the defaults.

## Persistence (`message_tags_archive.classifier_attributes`)

Stored as JSON in either form:

1. **Legacy:** `["education", "spam"]` (example keys) — interpreted as weight **1.0** for each listed tag when read.
2. **Current:** `{"attributes": [...], "weights": {"education": 0.92, "spam": 0.88, ...}}` — same idea with arbitrary catalog keys; **`attributes`** drive rules at write time; **`weights`** holds per-tag scores (including tags that scored below threshold, when the model supplied weights).

Helpers: `classifier.encode_classifier_blob`, `classifier.decode_classifier_blob`.

## Training UI

On **`/message/<rowid>`**, column **W** shows the LLM **weight** (two decimal places) for each tag row when present. The API payload also includes **`classifier_weights`** for the full map.

## Related scripts

- **`main.py`** — sets `Message.attributes` and `Message.attribute_weights` from `ClassificationResult`.
- **`scripts/dry_run_recent.py`** — full mode can print a **Weights:** line per message.
- **`scripts/reclassify_archive_tags.py`** — refreshes archive rows using the same classifier and JSON shape.

See also [QUERIES.md](QUERIES.md) for preview commands that hit the classifier, and [FRAMEWORK.md](FRAMEWORK.md) for the overall design.
