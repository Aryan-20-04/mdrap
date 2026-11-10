"""
MDRAP Strategy Execution Backends (§26 & §27).

Provides unified execution interfaces for:
- Historical deterministic backtesting (A)
- Live paper trading with real feeds + local matching engine (B)
- Exchange testnet execution (C)
"""

from .base import ExecutionBackend
from .historical import HistoricalSimulationBackend
from .paper import LivePaperTradingBackend
from .testnet import ExchangeTestnetBackend

__all__ = [
    "ExecutionBackend",
    "HistoricalSimulationBackend",
    "LivePaperTradingBackend",
    "ExchangeTestnetBackend",
]
