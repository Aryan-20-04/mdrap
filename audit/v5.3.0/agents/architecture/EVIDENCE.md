# Architecture & System Design — Exact Evidence

## 1. Version Inconsistency
- File: `src/mdrap/_version.py:8`
  ```python
  __version__ = "4.0.1"
  ```
- File: `pyproject.toml:6, 128`
  ```toml
  dynamic = ["version"]
  [tool.setuptools.dynamic]
  version = {attr = "mdrap._version.__version__"}
  ```
- File: `CHANGELOG.md:6-30`
  ```markdown
  ## [5.3.0] - 2026-11-05
  ...
  ## [5.0.0] - 2026-10-31
  ```
- File: `src/mdrap/api.py:99`
  ```python
  class HealthResponse(BaseModel):
      status: str = "healthy"
      version: str = __version__
  ```
- File: `audit/claims_manifest.json:2`
  ```json
  "mdrap_version": "4.0.1"
  ```

## 2. Options Fallback Shim Crashing CLI
- File: `src/mdrap/trading_cli.py:302`
  ```python
  from .options import OptionContract, OptionType, ExerciseStyle, price_option, OptionsChain
  ```
- File: `src/mdrap/options.py:46-59`
  ```python
  class OptionsChain:
      def __init__(self, *args, **kwargs):
          raise NotImplementedError("OptionsChain requires the 'mdrap_options' companion package...")
  class BlackScholes: pass
  class BinomialTree: pass
  __all__ = ["OptionsChain", "BlackScholes", "BinomialTree"]
  ```

## 3. Backtest Crash on StrategyRunner Stub
- File: `src/mdrap/backtest.py:100-101`
  ```python
  runner = StrategyRunner(strategy)
  runner.strategy.on_start()
  ```
- File: `src/mdrap/strategy_sdk.py:63-65`
  ```python
  class StrategyRunner:
      def __init__(self, *args, **kwargs):
          pass
  ```

## 4. Configuration Schema Restriction
- File: `src/mdrap/config_loader.py:56`
  ```python
  ALLOWED_TOP_SECTIONS = {"defaults", "venue", "system", "storage", "network", "security"}
  ```

## 5. Rich Dependency in CLI
- File: `src/mdrap/trading_cli.py:15-17`
  ```python
  from rich.console import Console
  from rich.panel import Panel
  from rich.table import Table
  ```
- File: `src/mdrap/cli/__init__.py:1937-1942`
  ```python
  try:
      from ..trading_cli import add_trading_parsers
      add_trading_parsers(sub)
  except Exception:
      pass
  ```\n