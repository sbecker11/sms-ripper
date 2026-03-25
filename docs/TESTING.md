# Testing

Tests live under `tests/` and use **pytest**. They avoid touching your real `chat.db`, the network (except mocked HTTP for the classifier), and AppleScript where possible.

## Prerequisites

Complete [SETUP.md](SETUP.md) through the virtual environment and dependencies:

```bash
cd /path/to/sms-ripper
source venv/bin/activate
pip install -r requirements.txt
```

You do **not** need a valid `ANTHROPIC_API_KEY` in `.env` for the default test suite: classifier tests patch `config` and mock the API.

## Project scripts (`poe`)

Like npm’s `package.json` scripts, tasks are defined in **`pyproject.toml`** under `[tool.poe.tasks]` and run with **[poethepoet](https://github.com/nat-n/poethepoet)** (`poe`), installed via `requirements.txt`.

From the **project root**, with the venv activated:

| Command | What it runs |
|--------|----------------|
| `poe test` | `pytest tests/ -v` |
| `poe test-quiet` | `pytest tests/ -q` |
| `poe cov` | Tests + coverage (≥80%, terminal report) |
| `poe cov-html` | Same as `cov`, plus `htmlcov/` |
| `poe dry-run` | `python main.py --dry-run` |
| `poe run-political` | `python main.py` (pass flags directly: `poe run-political --lookback 10080 --limit 500` — no `--` before the flags) |
| `poe preview-recent` | `scripts/dry_run_recent.py` — tags, rules, **Actions (execution)** (read-only DB; Claude unless `*-offline`) |
| `poe preview-recent-offline` | Same with **`--no-classify`** |
| `poe preview-recent-compact` | **`--compact`** output |
| `poe preview-recent-compact-offline` | Compact + **`--no-classify`** |
| `poe backup-db` | Timestamped `chat.db` copy under **`backups/`** |
| `poe quit-messages` | Quit Messages.app if running |
| `poe echo-cd-root` | Print `cd` to repo root |

**`chat.db` read-only SQL reports:** `poe query-total`, `poe query-text`, `poe query-recent`, `poe query-recent-tags` (Claude per message unless `query-recent-tags-offline`), `poe query-top-chats`, and more — see [QUERIES.md](QUERIES.md).

List tasks: `poe --help` or `poe`.

## Running tests (pytest directly)

Always run pytest from the **project root** (directory containing `main.py`, `pytest.ini`, and `pyproject.toml`):

```bash
pytest tests/ -v
```

Quiet summary:

```bash
pytest tests/ -q
```

### Why `pytest.ini` exists

`pytest.ini` sets:

```ini
[pytest]
pythonpath = .
```

That adds the project root to Python’s import path so `import config`, `import reader`, etc. work when collecting tests. If you see `ModuleNotFoundError: No module named 'config'`, you are usually running pytest from the wrong directory or without this config.

## Coverage

The project uses **pytest-cov** with settings in **`pyproject.toml`** (`[tool.coverage.*]`):

- **Branch coverage** is enabled.
- **`tests/*`** is omitted from coverage collection.
- **`fail_under = 80`** applies when you use Coverage’s reporting options (see below).

### Standard coverage command (recommended)

Shortcut (same flags as below):

```bash
poe cov
```

Run all tests and enforce **at least 80%** total coverage on the application modules:

```bash
pytest tests/ \
  --cov=reader \
  --cov=classifier \
  --cov=rules \
  --cov=actions \
  --cov=config \
  --cov=main \
  --cov-report=term-missing \
  --cov-fail-under=80
```

- **`--cov-report=term-missing`** — lists lines not executed.
- **`--cov-fail-under=80`** — exits with failure if total coverage is below 80%.

Configuration in `pyproject.toml` (`show_missing`, `precision`, `omit`, `branch`) is picked up by Coverage when invoked through pytest-cov.

### HTML report

Shortcut:

```bash
poe cov-html
open htmlcov/index.html
```

Full command:

```bash
pytest tests/ \
  --cov=reader \
  --cov=classifier \
  --cov=rules \
  --cov=actions \
  --cov=config \
  --cov=main \
  --cov-report=term-missing \
  --cov-report=html \
  --cov-fail-under=80
```

Then open the report: `open htmlcov/index.html`

The `htmlcov/` directory is generated locally; add it to `.gitignore` if it is not already ignored.

## What the tests cover

| Area       | File(s)                    | Notes                                                                                               |
| ---------- | -------------------------- | --------------------------------------------------------------------------------------------------- |
| Reader     | `tests/test_reader.py`     | Apple timestamp helpers, SQL query behavior on a **temporary** SQLite DB, filters, missing DB error |
| Rules      | `tests/test_rules.py`      | `rules.evaluate` for attribute → action mapping                                                     |
| Classifier | `tests/test_classifier.py` | Mocked `urllib` responses, HTTP errors, missing API key                                             |
| Actions    | `tests/test_actions.py`    | Blocklist, dry-run, mocked `subprocess` / AppleScript paths; **archive-before-delete** execution order |
| Archive    | `tests/test_archive.py`    | `POLITICAL_archive` copy/delete on a temp DB                                                       |
| Dry preview | `tests/test_dry_run_recent.py` | Subprocess smoke tests for `scripts/dry_run_recent.py`                                         |
| Main       | `tests/test_main.py`       | `process_once` branches with mocked reader/classifier/actions; CLI flags and loop behavior          |

The `if __name__ == "__main__"` block in `main.py` is marked with `# pragma: no cover` because it only runs when you execute `python main.py`, not when tests import the module.

## Caches and artifacts

- **`.pytest_cache/`** — pytest cache (safe to delete; often gitignored).
- **`htmlcov/`** — HTML coverage output when using `--cov-report=html`.

## Troubleshooting

| Issue                                   | What to try                                                                                                                    |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `No module named 'config'`              | Run pytest from the repo root; ensure `pytest.ini` is present with `pythonpath = .`.                                           |
| Coverage fails under 80%                | Run with `--cov-report=term-missing` and add tests for the listed lines, or adjust scope if you intentionally exclude modules. |
| Tests pass but agent fails on real data | Tests do not use your real `chat.db`; check Full Disk Access and paths in [SETUP.md](SETUP.md).                                |

---

For installation and permissions, see [SETUP.md](SETUP.md).
