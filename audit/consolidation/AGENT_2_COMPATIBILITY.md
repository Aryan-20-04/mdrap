# MDRAP Consolidation — Agent 2: Legacy Root-Level Import Shim Removal & Compatibility

## 1. Inventory & Root-Level Topology

The repository contains 94 files at `src/*.py`:
- 1 package root (`src/__init__.py`)
- 1 build utility (`src/mdrap_build.py`)
- 92 forwarding shims implementing dynamic delegation:
  ```python
  import importlib as _importlib
  import sys as _sys
  _module = _importlib.import_module("mdrap.<modulename>")
  _sys.modules[__name__] = _module
  ```

---

## 2. Packaging vs Repository Root Importing

A critical discovery of this audit:
1. **Installed Wheel Packages Do NOT Include `src/*.py`**:
   The setuptools configuration in `pyproject.toml` is:
   ```toml
   [tool.setuptools.packages.find]
   where = ["src"]
   include = ["mdrap", "mdrap.*"]
   ```
   When a user installs MDRAP via `pip install mdrap-core` or from a `.whl` distribution, setuptools includes **only** the `mdrap` directory. None of the 92 root forwarding shims were ever installed into `site-packages`.
2. **The Only Supported Public API is `mdrap.*`**:
   Any external caller using `pip install` has always had to write:
   ```python
   from mdrap import Engine, CanonicalEvent, Store
   from mdrap.models import QualityStatus
   ```
   The root-level files in `src/*.py` existed solely as local development shortcuts when running Python with `sys.path.insert(0, "src")`.

---

## 3. Internal References & Test Migration

Across 311 test files and internal tooling, only 5 modules in `src/*.py` had remaining imports:
1. `_version`: Imported as `from _version import __version__` in `tests/test_api_endpoints.py`, `tests/test_docs_verification.py`, and `tests/test_sdk_commercial.py`.
   - **Migration**: Change to `from mdrap._version import __version__`.
2. `cli`: Imported as `from cli import build_parser, main` in `tests/test_cli.py` and `tests/test_chd.py`.
   - **Migration**: Change to `from mdrap.cli import build_parser, main`.
3. `features`, `fix_engine`, `options`, `risk`: Imported as `import options, features, risk, fix_engine` in `tests/test_fastpath_quantitative.py`.
   - **Migration**: Change to `from mdrap import options, features, risk, fix_engine`.
4. `src/__init__.py`: Contained legacy imports `from client import ...`, `from models import ...`.
   - **Migration**: Cleaned to import from `.client` and `.models` (or `mdrap.client`, `mdrap.models`).

---

## 4. Name Collision & Clean Namespace Guarantees

Removing `src/*.py` eliminates significant Python namespace pollution:
- `src/security.py` collided with third-party or local security packages.
- `src/models.py` collided with general ML/database model namespaces.
- `src/config.py` collided with standard config modules.
- `src/client.py` and `src/rules.py` collided with common application modules.

By consolidating all imports under `mdrap.*`:
- Every module has an unambiguous, fully-qualified Python module path: `mdrap.gateway`, `mdrap.quality`, `mdrap.reconciliation`, `mdrap.provenance`, `mdrap.storage`, `mdrap.incidents`, `mdrap.replay`, `mdrap.security`.
- No root-level shadowing can occur when MDRAP is included as a git submodule or dependency.

---

## 5. Deletion & Deprecation Strategy

1. **Retain in `src/`**:
   - `src/mdrap_build.py`: Required by `pyproject.toml` (`build_py = "mdrap_build.CleanBuildPy"`).
   - `src/__init__.py`: Kept clean as an import router to `mdrap`.
2. **Delete from `src/*.py`**:
   - All 92 unneeded forwarding shims.
3. **Preserve Root Entry Point**:
   - `cli.py` in the workspace root (`./cli.py`) is retained so developers can run `python cli.py <command>` locally, while `mdrap` is the installed console script via `pyproject.toml`.
