"""
Tests for Execution Backends (§26 & §27).

Verifies:
- HistoricalSimulationBackend: Deterministic event replay.
- LivePaperTradingBackend: Live market event processing with local matching.
- ExchangeTestnetBackend: Testnet endpoint validation, API key masking,
  and STRICT prohibition of production endpoints.
"""

import pytest
import sys
from pathlib import Path

# Add paths
root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "packages" / "mdrap-strategies"))
sys.path.insert(0, str(root / "src"))

from mdrap_strategies.backends import (
    HistoricalSimulationBackend,
    LivePaperTradingBackend,
    ExchangeTestnetBackend,
)
from mdrap_strategies.backends.testnet import SecurityViolationError
from mdrap_strategies.matching_engine import (
    MatchingEngineConfig,
    SimOrderSide,
    SimOrderType,
    SimOrderStatus,
)
from mdrap_strategies.risk_engine import InstitutionalRiskEngine, StrictRiskLimits
from mdrap.models import CanonicalEvent, EventType, QualityStatus


def test_historical_simulation_backend():
    """Verify backend A replays events deterministically."""
    risk = InstitutionalRiskEngine(limits=StrictRiskLimits())
    config = MatchingEngineConfig(network_latency_ms=0.0, matching_delay_ms=0.0)
    backend = HistoricalSimulationBackend(risk_engine=risk, matching_config=config)
    backend.start()
    assert backend.is_running is True

    # Seed book via quote event
    q_evt = CanonicalEvent(
        event_id=1,
        source="HIST",
        source_id=1,
        instrument_id="AAPL",
        event_type=EventType.QUOTE,
        exchange_timestamp=1.0,
        receive_timestamp=1.0,
        bid_price=150.0,
        ask_price=150.1,
        bid_size=100.0,
        ask_size=100.0,
        quality_status=QualityStatus.VALID,
    )
    backend.on_market_event(q_evt)

    # Submit order (starts in PENDING status while in flight)
    order = backend.submit_order(
        symbol="AAPL",
        side=SimOrderSide.BUY,
        order_type=SimOrderType.LIMIT,
        price=150.0,
        quantity=50.0,
    )
    assert order is not None
    assert order.status == SimOrderStatus.PENDING

    # Replay trade to trigger fill
    t_evt = CanonicalEvent(
        event_id=2,
        source="HIST",
        source_id=2,
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=2.0,
        receive_timestamp=2.0,
        price=150.0,
        quantity=150.0,
        quality_status=QualityStatus.VALID,
    )
    fills = backend.on_market_event(t_evt)
    assert len(fills) >= 1
    assert order.status == SimOrderStatus.FILLED
    assert order.filled_quantity == 50.0

    backend.stop()
    assert backend.is_running is False


def test_live_paper_trading_backend():
    """Verify backend B runs live paper trading with local simulation."""
    risk = InstitutionalRiskEngine(limits=StrictRiskLimits())
    backend = LivePaperTradingBackend(risk_engine=risk)
    backend.start()
    assert backend.is_running is True

    # Seed live quote
    q_evt = CanonicalEvent(
        event_id=10,
        source="BINANCE",
        source_id=10,
        instrument_id="BTC-USDT",
        event_type=EventType.QUOTE,
        exchange_timestamp=1.0,
        receive_timestamp=1.0,
        bid_price=60_000.0,
        ask_price=60_010.0,
        bid_size=1.0,
        ask_size=1.0,
        quality_status=QualityStatus.VALID,
    )
    backend.on_market_event(q_evt)

    order = backend.submit_order(
        symbol="BTC-USDT",
        side=SimOrderSide.BUY,
        order_type=SimOrderType.LIMIT,
        price=60_000.0,
        quantity=0.1,
    )
    assert order is not None
    assert backend.get_open_orders() != []

    backend.stop()
    assert backend.is_running is False


def test_exchange_testnet_backend_security_block():
    """
    CRITICAL SECURITY TEST:
    Verify ExchangeTestnetBackend strictly BLOCKS production endpoints.
    Attempts to pass a production URL or live credentials must raise SecurityViolationError.
    """
    risk = InstitutionalRiskEngine(limits=StrictRiskLimits())

    # 1. Allowed testnet host
    testnet = ExchangeTestnetBackend(
        risk_engine=risk,
        api_key="test_key_abc",
        api_secret="test_secret_123",
        base_url="https://testnet.binance.vision",
    )
    testnet.start()
    assert testnet.is_running is True
    # Verify API key masking in string representation / status
    status = testnet.get_status()
    assert "test_secret_123" not in str(status)
    testnet.stop()

    # 2. Block production URL (Binance Production)
    with pytest.raises(SecurityViolationError, match="FATAL: Production URL"):
        ExchangeTestnetBackend(
            risk_engine=risk,
            api_key="key",
            api_secret="secret",
            base_url="https://api.binance.com",  # PRODUCTION! MUST BE REJECTED
        )

    # 3. Block production URL (Coinbase Production)
    with pytest.raises(SecurityViolationError, match="FATAL: Production URL"):
        ExchangeTestnetBackend(
            risk_engine=risk,
            api_key="key",
            api_secret="secret",
            base_url="https://api.exchange.coinbase.com",  # PRODUCTION!
        )
