# MDRAP Consolidation — Remaining Risks & Mitigation Strategies

This document identifies remaining technical and operational risks following the product consolidation on branch `cleanup/product-scope-consolidation`.

---

## 1. Risk Register

| # | Risk Description | Severity | Likelihood | Impact | Mitigation Strategy |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **R-01** | **Legacy Internal Scripts Importing `src/*.py` directly** | LOW | LOW | Scripts running from repository root that wrote `import bbo` or `import storage` instead of `from mdrap import ...` will fail. | All 27 internal benchmark, example, and test scripts have been migrated to `from mdrap import ...`. `cli.py` in the workspace root remains in place as a developer runner that delegates directly to `mdrap.cli`. |
| **R-02** | **Operators Expecting YAML Configs Without `pyyaml`** | LOW | LOW | If an operator deploys a minimal container with a custom `config.yaml` but without installing `[yaml]`, advanced YAML tags might not parse. | `src/mdrap/config.py` includes a robust built-in stdlib fallback parser capable of parsing standard key-value structures. Furthermore, `config_loader.py` natively supports `mdrap.toml` via Python's standard library `tomllib`, which is recommended for all new deployments. |
| **R-03** | **Deprecation Confusion on Retired Commands (`nav`, `desk`)** | LOW | LOW | Existing operators running `mdrap desk` or `mdrap nav` might expect the interactive TUI. | Rather than crashing with an unhandled parser error, `cmd_desk` displays an explicit notice informing operators that the interactive desk has been retired and points them to `mdrap status`, `mdrap doctor`, and `mdrap replay`. |
| **R-04** | **Downstream OLAP Queries Without `[columnar]` Extra** | LOW | LOW | If an operator runs `mdrap run --duckdb` without installing `duckdb`, an unhandled exception could occur. | `cmd_run` handles missing DuckDB gracefully with an informational message directing the operator to install `pip install mdrap-core[columnar]`. SQLite WAL operates as the zero-dependency canonical store. |
| **R-05** | **Companion Package Installation Guidance** | LOW | LOW | Users seeking options derivatives pricing or AIS maritime tracking might look for `mdrap.options` or `mdrap.vessel`. | `src/mdrap/options.py` and `src/mdrap/vessel.py` emit explicit deprecation warnings and raise `CompanionPackageMissing` with exact pip install commands for `packages/mdrap-options` and `packages/mdrap-contrib-vessel`. |

---

## 2. Release & Deployment Recommendations

1. **Tagging**: Tag the consolidated state as `v5.4.0-consolidated` once merged into `main` and `product-focus/mdrap-reliability-platform`.
2. **PyPI Distribution**: Publish `dist/mdrap_core-5.3.0-py3-none-any.whl` and `dist/mdrap_core-5.3.0.tar.gz`.
3. **Documentation Sync**: Ensure getting started guides recommend `mdrap.toml` and reference the 10 core operational CLI commands.
