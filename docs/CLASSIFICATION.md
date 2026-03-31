# Classification and tag weighting

## What kind of problem this is

The agent treats each message as **multi-label** classification: **several tags can apply at once** (e.g. both `POLITICAL` and `SPAM`). Each tag has its own score in **[0, 1]**, interpreted as **confidence that the tag applies**.

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

## POLITICAL keyword merge

If the model did not output `POLITICAL` but the message matches built-in political **keyword / regex** heuristics, the pipeline **adds** `POLITICAL` and sets its weight to at least **`HEURISTIC_POLITICAL_WEIGHT`** (default **0.88**). That is independent of the model’s other scores.

## Persistence (`POLITICAL_archive.classifier_attributes`)

Stored as JSON in either form:

1. **Legacy:** `["POLITICAL", "SPAM"]` — interpreted as weight **1.0** for each listed tag when read.
2. **Current:** `{"attributes": [...], "weights": {"POLITICAL": 0.92, "SPAM": 0.88, ...}}` — **`attributes`** are the tags used for rules at write time; **`weights`** holds per-tag scores (including tags that scored below threshold, when the model supplied weights).

Helpers: `classifier.encode_classifier_blob`, `classifier.decode_classifier_blob`.

## Training UI

On **`/message/<rowid>`**, column **W** shows the LLM **weight** (two decimal places) for each tag row when present. The API payload also includes **`classifier_weights`** for the full map.

## Related scripts

- **`main.py`** — sets `Message.attributes` and `Message.attribute_weights` from `ClassificationResult`.
- **`scripts/dry_run_recent.py`** — full mode can print a **Weights:** line per message.
- **`scripts/reclassify_archive_tags.py`** — refreshes archive rows using the same classifier and JSON shape.

See also [QUERIES.md](QUERIES.md) for preview commands that hit the classifier.
