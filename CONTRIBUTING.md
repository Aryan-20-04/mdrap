# Contributing to MDRAP

Guidelines for contributing to the Market Data Reliability & Acceleration Platform.

## Prerequisites

- Python 3.10+
- GCC or Clang (optional, required for native C fastpath acceleration)

## Setup

```bash
git clone https://github.com/Aryan-20-04/mdrap.git
cd mdrap
pip install -r requirements.txt
python build_fastpath.py
```

## Running Tests

Run the test suite with a 30-second timeout guard:

```bash
pytest tests/ -v --timeout=30
```

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

## Good First Issues

- **Tests**: Add unit or edge-case tests in [`tests/`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/tests/).
- **Quality Rules**: Implement or refine data validation rules in [`src/quality.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/quality.py).
- **CLI Commands**: Add or improve CLI commands and reporting in [`src/cli.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/cli.py).

## Pull Request Process

1. Create a focused branch for your change.
2. Verify all tests pass: `pytest tests/ -v --timeout=30`.
3. Verify style is clean: `ruff check src/` and `ruff format --check src/`.
4. Submit your pull request with a concise summary of changes.
