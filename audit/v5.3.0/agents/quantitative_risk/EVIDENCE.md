# Quantitative, Trading Risk & Strategy — Exact Evidence

## 1. Broken Import in `mdrap-strategies`
- File: `packages/mdrap-strategies/mdrap_strategies/__init__.py:1596-1601`
  ```python
  from .backends import (
      ExecutionBackend,
      HistoricalSimulationBackend,
      LivePaperTradingBackend,
      ExchangeTestnetBackend,
  )
  ```
  (File `packages/mdrap-strategies/mdrap_strategies/backends.py` does not exist).

## 2. Walk-Forward Dummy Implementation
- File: `src/mdrap/backtest.py:370-377`
  ```python
  train_size = int(len(window_events) * train_pct)
  test_events = window_events[train_size:]
  if not test_events:
      continue
  strategy = strategy_factory()
  res = self.run(strategy, test_events)
  results.append(res)
  ```

## 3. Silent 1.0 Parity Fallback
- File: `src/mdrap/fx.py:86-89`
  ```python
  if usd_per_from is None or usd_per_to is None:
      # Unknown currency, assume 1.0 parity
      return 1.0
  ```\n