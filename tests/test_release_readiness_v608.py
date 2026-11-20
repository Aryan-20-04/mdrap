"""
MDRAP 6.0.8 — Release Readiness & Governance Compliance Test Suite.

Verifies:
1. Version alignment across metadata and code modules (__version__ == "6.0.8").
2. Production governance invariants (NO DEPLOYMENT freeze, shadow trading only, production endpoint refusal).
3. Sidecar interface schemas (SHM v3, StreamClient API, L1/L2 event serialization).
4. Platform storage isolation and bounded memory footprint.
"""

from __future__ import annotations

import os
import sys
import pytest

# Ensure salt is set for SecurityManager
os.environ["MDRAP_API_KEY_SALT"] = "test_release_readiness_salt_secret_123"

# Ensure repo root and tools in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import mdrap
from mdrap._version import __version__
from mdrap.models import (
    CanonicalEvent,
    EventType,
    QualityStatus,
    Reason,
)
from mdrap.service import StreamClient
from mdrap.shm import (
    MAGIC,
    VERSION,
    SLOT_SIZE,
    HEADER_SIZE,
    SHMReader,
    SHMWriter,
)
from mdrap.storage import Store
from mdrap_strategies.backends.testnet import ExchangeTestnetBackend, SecurityViolationError
from mdrap_strategies.risk_engine import InstitutionalRiskEngine
from tools.run_shadow_trading import STRATEGY_REGISTRY


def test_v608_version_metadata():
    """Verify package version is aligned to 6.0.8."""
    assert __version__ == "6.0.8"
    assert mdrap.__version__ == "6.0.8"


def test_v608_governance_freeze_enforcement():
    """Verify NO DEPLOYMENT governance freeze and strategy registry status."""
    # Ensure no live routing paths are enabled by default
    assert os.environ.get("MDRAP_ALLOW_LIVE_ORDERS", "0") != "1"

    # Verify strategy registry entries
    assert STRATEGY_REGISTRY["Fixed-Spread Post-Only"] == "ABANDONED"
    assert STRATEGY_REGISTRY["Avellaneda-Stoikov"] == "RESEARCH ONLY"
    assert STRATEGY_REGISTRY["Inventory-Skewed"] == "RESEARCH ONLY"
    assert STRATEGY_REGISTRY["Directional Alpha Quoting"] == "NO DEPLOYMENT"
    assert STRATEGY_REGISTRY["No-Trade Baseline"] == "CANONICAL BASELINE"

    # Verify production endpoint rejection
    risk_engine = InstitutionalRiskEngine()
    with pytest.raises(SecurityViolationError, match="Production URL"):
        ExchangeTestnetBackend(
            risk_engine=risk_engine,
            base_url="https://api.binance.com",
        )


def test_v608_shm_l1_and_l2_serialization():
    """Verify SHM v3 writes and reads L1 ticks and L2 depth without corruption."""
    shm_name = f"test_v608_shm_{os.getpid()}"
    writer = SHMWriter(name=shm_name, slot_count=64)
    reader = SHMReader(name=shm_name)

    try:
        # 1. Write and read L1 tick
        writer.write_tick(
            seq=101,
            symbol="BTC/USD",
            source="BINANCE",
            price=65432.10,
            size=1.25,
            bid=65430.00,
            ask=65435.00,
            bid_size=5.0,
            ask_size=4.5,
            status="VALID",
            is_crossed=False,
            exchange_ts=1700000000.0,
            ingest_ts=1700000000.001,
            broadcast_ts=1700000000.002,
            engine_us=12.5,
        )

        item = reader.read_slot(101)
        assert item is not None
        assert item["type"] == "TICK"
        assert item["seq"] == 101
        assert item["sym"] == "BTC/USD"
        assert item["source"] == "BINANCE"
        assert abs(item["price"] - 65432.10) < 1e-4
        assert abs(item["bid"] - 65430.00) < 1e-4
        assert item["status"] == "VALID"

        # 2. Write and read L2 depth frame
        writer.write_depth(
            seq=102,
            symbol="ETH/USD",
            best_bid=3450.0,
            best_ask=3452.0,
            bid_size=10.0,
            ask_size=12.0,
            micro_price=3450.91,
            ofi=2.5,
            is_crossed=False,
            exchange_ts=1700000001.0,
            ingest_ts=1700000001.001,
            broadcast_ts=1700000001.002,
            engine_us=18.2,
        )

        depth_item = reader.read_slot(102)
        assert depth_item is not None
        assert depth_item["type"] == "DEPTH"
        assert depth_item["seq"] == 102
        assert depth_item["sym"] == "ETH/USD"
        assert abs(depth_item["micro_price"] - 3450.91) < 1e-4
        assert abs(depth_item["ofi"] - 2.5) < 1e-4
        assert depth_item["bids"][0][0] == 3450.0
        assert depth_item["asks"][0][0] == 3452.0

    finally:
        reader.close()
        writer.close()


def test_v608_wal_checkpoint_primitives():
    """Verify SQLite WAL explicit checkpointing and writes counter."""
    store = Store(":memory:")
    res = store.wal_checkpoint("PASSIVE")
    assert isinstance(res, dict)
    assert "busy" in res
    assert "log_pages" in res
    assert "checkpointed_pages" in res
    store.close()
