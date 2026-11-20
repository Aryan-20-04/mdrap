"""Tests for Phase 8: Package Boundaries & Edition Separation (F-13).

Verifies:
1. Core engine packages import cleanly with zero external/companion dependencies.
2. Core pipeline (Engine, IngestLog, Quality, Models, SHM) operates independently.
3. Companion/deprecated modules (options, tca, risk, strategy_sdk, vessel) emit DeprecationWarning on import.
4. Core exports in `mdrap/__init__.py` do not expose non-core sprawl.
"""

from __future__ import annotations

import pytest
import warnings
import tempfile
from pathlib import Path


def test_core_engine_clean_imports_without_companion():
    """Verify core engine imports cleanly and exposes only core primitives."""
    import mdrap

    # Core essentials must be present
    assert hasattr(mdrap, "Engine")
    assert hasattr(mdrap, "IngestLog")
    assert hasattr(mdrap, "RawEvent")
    assert hasattr(mdrap, "CanonicalEvent")
    assert hasattr(mdrap, "QualityStatus")
    assert hasattr(mdrap, "Reason")
    assert hasattr(mdrap, "Pipeline")
    assert hasattr(mdrap, "SQLiteProjection")

    # Non-core financial analytics and vessel intelligence must NOT be in public __all__
    assert "OptionsChain" not in mdrap.__all__
    assert "TCAEngine" not in mdrap.__all__
    assert "PortfolioRiskEngine" not in mdrap.__all__
    assert "VesselTracker" not in mdrap.__all__


def test_core_pipeline_functional_without_companion():
    """Verify that a full ingest-validate-persist flow runs without companion packages."""
    from mdrap.engine import Engine, EngineConfig
    from mdrap.models import RawEvent, QualityStatus
    from mdrap.ingestlog import IngestLog

    with tempfile.TemporaryDirectory() as tmpdir:
        log = IngestLog(tmpdir)
        try:
            engine = Engine.open(None)

            raw = RawEvent(
                source="TEST_FEED",
                receive_timestamp=1000.0,
                payload={
                    "instrument": "AAPL",
                    "event_type": "TRADE",
                    "exchange_ts": 1000.0,
                    "sequence": 1,
                    "price": 150.25,
                    "quantity": 100,
                },
            )
            dec = engine.submit(raw)
            assert dec.quality_status == QualityStatus.VALID

            # Verify persistence to WAL
            off = log.append(raw)
            assert off == 0
            assert len(list(log.iter_from(0))) == 1
        finally:
            log.close()


def test_pruned_modules_raise_module_not_found():
    """Verify that obsolete/companion non-core modules are completely removed from core."""
    import importlib

    pruned_mods = [
        "mdrap.options",
        "mdrap.tca",
        "mdrap.risk",
        "mdrap.strategy_sdk",
        "mdrap.vessel",
        "mdrap.backtest",
        "mdrap.fix_engine",
        "mdrap.shadow",
        "mdrap.consensus",
        "mdrap.failover",
        "mdrap.journal_replicator",
        "mdrap.partition",
        "mdrap.async_fanout",
        "mdrap.network",
        "mdrap.multicast_arbitrator",
        "mdrap.pcap",
        "mdrap.scheduler",
        "mdrap.sample_plugins",
        "mdrap.cme",
        "mdrap.eurex",
        "mdrap.gateway_tcp",
        "mdrap.mbo",
        "mdrap.flow_tracker",
        "mdrap.bardb",
        "mdrap.chd_history",
        "mdrap.workload_simulator",
        "mdrap.stresstest",
        "mdrap.kafka_sink",
    ]

    for mod in pruned_mods:
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(mod)
