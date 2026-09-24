# Contributing to MDRAP

Guidelines for contributing to the Market Data Reliability & Acceleration Platform.

## Prerequisites

- Python 3.10+
- GCC or Clang (optional, required for native C fastpath acceleration)

## Setup

```bash
git clone https://github.com/Aryan-20-04/mdrap.git
cd mdrap
pip install -e ".[all]"
python build_fastpath.py
```

> [!TIP]
> - **Full Development Environment (Recommended)**: `pip install -e ".[all]"` (or `pip install -r requirements.txt`) installs all development tools (`pytest`, `pytest-asyncio`, `pytest-timeout`), REST/WebSocket layers (`fastapi`, `uvicorn`, `httpx`), and columnar analytics (`pyarrow`, `duckdb`).
> - **Zero-Dependency Core**: `pip install -e .` runs the pipeline, validation, and storage using only Python standard library. Optional API test suites gracefully skip when optional dependencies are absent.

## Running Tests

Run the test suite with a 30-second timeout guard:

```bash
pytest tests/ -v --timeout=30
```

> [!IMPORTANT]
> All tests must pass cleanly (`pytest tests/ -v`) prior to opening or merging any pull request. Never bypass test failures.

## Code Style

Lint and format code using Ruff:

```bash
ruff check src/
ruff format src/
```

## Project Rules

- **Pure Python + rich only**: Standard library only; `rich` is the sole UI dependency.
- **No Async / No Threading**: Pipeline execution remains synchronous for V1.
- **CLI-Only**: No HTTP, REST, or WebSocket services for V1.
- **Never Drop Bad Data**: Quarantine invalid or malformed data; never discard silently.
- **Quality Hierarchy**: `INVALID` > `SUSPICIOUS` > `VALID` (status never downgrades).
- **ID Generation**: Use `itertools.count()`, not `uuid.uuid4()`.

## Extension Development

MDRAP provides standardized extension interfaces for integrating external venues, custom validation logic, and alternate persistence engines without altering core engine internals. Refer to the [Architecture Map](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/docs/map.md) for module orientation.

### 1. Adding a New Feed Adapter
Exchange and venue integrations implement the [`FeedAdapter`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/adapters/__init__.py) protocol (`open()`, `__iter__()`, `close()`) and emit standardized [`RawEvent`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/models.py) objects. Adapters are discovered dynamically at runtime via the `mdrap.adapters` entry point group.
- Guide: [Feed Adapter Development](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/docs/extending/feed-adapter.md)
- Reference: [`src/adapters/__init__.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/adapters/__init__.py)

### 2. Adding a Custom Quality Rule
User-defined quality validation rules are registered in the user bitmask range (bits 32–63) using the [`@register_rule`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/rules.py) decorator from [`src/rules.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/rules.py). User rules run post-native evaluation and cannot crash the pipeline or downgrade existing anomaly statuses.
- Guide: [Custom Quality Rules](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/docs/extending/quality-rules.md)
- Reference: [`src/rules.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/rules.py)

### 3. Implementing a Storage Backend
Custom persistence engines (e.g. Parquet, cloud object stores, or time-series databases) implement the [`StorageBackend`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/protocols.py) protocol defined in [`src/protocols.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/protocols.py), providing swappable archival and retrieval interfaces.
- Guide: [Storage Backend Implementation](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/docs/extending/storage-backend.md)
- Reference: [`src/protocols.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/protocols.py)

## Stability Labels

Every Python module in `src/` must declare an explicit stability contract via the module-level `__stability__` attribute:

```python
__stability__ = "stable"  # "stable" | "beta" | "experimental"
```

- **`stable`**: Public API is frozen. Zero breaking changes without a formal deprecation period (at least one minor release cycle emitting a `DeprecationWarning`).
- **`beta`**: Functionally complete and tested, but API ergonomics may evolve across minor versions.
- **`experimental`**: Active prototyping or exploratory research. No backward-compatibility guarantees; interfaces may change or be removed at any time.

> [!NOTE]
> All new modules submitted to MDRAP default to `"experimental"`. Promotion to higher tiers requires test coverage ($\ge 85\%$ for `beta`, $\ge 90\%$ for `stable`), complete documentation, and at least one production release cycle.
> For details, see [ADR 0005: Backbone Stability Policy](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/docs/decisions/0005-backbone-stability-policy.md).

## Good First Issues

- **Tests**: Add unit or edge-case tests in [`tests/`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/tests/).
- **Quality Rules**: Implement or refine data validation rules in [`src/quality.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/quality.py).
- **CLI Commands**: Add or improve CLI commands and reporting in [`src/cli.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/cli.py).

## Pull Request Process

1. Create a focused branch for your change.
2. Verify all tests pass: `pytest tests/ -v --timeout=30`.
3. Verify style is clean: `ruff check src/` and `ruff format --check src/`.
4. Ensure any new module declares its `__stability__` attribute.
5. Submit your pull request with a concise summary of changes and test results.
