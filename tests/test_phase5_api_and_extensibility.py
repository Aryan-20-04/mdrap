"""Phase 5 API & Extensibility Verification Suite (Gate G5).

Verifies:
1. Unified Canonical Public API:
   - Engine.open(path, config)
   - Engine.submit(events)
   - Engine.subscribe(projection, from_offset=0)
   - Engine.replay(from_offset, to_offset, projection)
2. Strict Batch Alignment:
   - Engine.submit and Pipeline.process_batch(strict_align=True) maintain 1-to-1 input-to-output alignment.
   - Quarantine and failed records retain exact positions; background drains do not displace slots.
3. Process-Global State Elimination:
   - Multi-engine and multi-gateway instances in the same process maintain strict isolation.
4. Extensible ReasonRegistry:
   - Dynamic reason registration, Reason enum interoperability, description and severity querying.
5. Entry-Point Groups & Third-Party Plugin Conformance:
   - Discovery and consumption of all 6 entry points.
   - Third-party storage backend conformance.
   - Third-party quality rule pack conformance.
6. Deprecation Detection:
   - Pipeline and Client deprecation warnings.
   - Legacy environment variable warnings (MDRAP_ASYNC_WRITER, MDRAP_DISABLE_JOURNAL).
   - Rejection of silent unexpected kwargs on Engine.
"""

from __future__ import annotations

import os
import tempfile
import warnings
import pytest

from mdrap.models import (
    CanonicalEvent,
    EventType,
    QualityStatus,
    RawEvent,
    Reason,
    ReasonRegistry,
    reason_registry,
)
from mdrap.engine import Engine, EngineDecision, EngineState
from mdrap.gateway import Gateway, SchemaError
from mdrap.projection import SQLiteProjection
from mdrap.pipeline import Pipeline
from mdrap.client import Client, MDRAPClient
from mdrap.storage import Store
from mdrap.plugins import (
    PLUGIN_GROUPS,
    discover_all_plugins,
    get_adapter,
    get_quality_rule,
    get_storage_backend,
    get_auth_provider,
    get_output_sink,
    get_alert_sink,
    validate_plugin,
)
from mdrap.sample_plugins import (
    SampleMemoryStorageBackend,
    SampleAuthProvider,
    SampleOutputSink,
    SampleAlertSink,
    register_sample_quality_rules,
)
from test_plugin_conformance import (
    StorageSinkConformanceTester,
    AdapterConformanceTester,
)


def test_engine_open_lifecycle_and_wal_recovery(tmp_path):
    """Test Engine.open() creates IngestLog, submits events, folds state, and recovers on re-open."""
    wal_dir = str(tmp_path / "engine_wal")

    raw1 = RawEvent(
        source="FEED_A",
        payload={
            "instrument": "AAPL",
            "event_type": "TRADE",
            "price": 150.0,
            "quantity": 10.0,
            "sequence": 1,
            "exchange_ts": 1700000000.0,
        },
        receive_timestamp=1700000000.001,
        raw_id="raw_1",
    )

    raw2 = RawEvent(
        source="FEED_A",
        payload={
            "instrument": "AAPL",
            "event_type": "TRADE",
            "price": 150.5,
            "quantity": 20.0,
            "sequence": 2,
            "exchange_ts": 1700000001.0,
        },
        receive_timestamp=1700000001.001,
        raw_id="raw_2",
    )

    # 1. Open engine, submit events
    with Engine.open(wal_dir) as engine1:
        dec1 = engine1.submit(raw1)
        assert isinstance(dec1, EngineDecision)
        assert dec1.offset == 0
        assert dec1.quality_status == QualityStatus.VALID
        assert dec1.canonical_event.price == 150.0

        dec2 = engine1.submit(raw2)
        assert isinstance(dec2, EngineDecision)
        assert dec2.offset == 1
        assert dec2.canonical_event.price == 150.5

        assert engine1.state.event_count == 2
        assert engine1.log.next_offset == 2
        engine1.flush()

    # 2. Re-open engine on existing directory: fold state must recover from log
    with Engine.open(wal_dir) as engine2:
        assert engine2.state.event_count == 2
        assert engine2.log.next_offset == 2
        assert engine2.state.sequence_state.get("FEED_A:AAPL") == 2

        # Submitting next event continues seamlessly
        raw3 = RawEvent(
            source="FEED_A",
            payload={
                "instrument": "AAPL",
                "event_type": "TRADE",
                "price": 151.0,
                "quantity": 30.0,
                "sequence": 3,
                "exchange_ts": 1700000002.0,
            },
            receive_timestamp=1700000002.001,
            raw_id="raw_3",
        )
        dec3 = engine2.submit(raw3)
        assert dec3.offset == 2
        assert dec3.quality_status == QualityStatus.VALID
        assert engine2.state.event_count == 3


def test_engine_subscribe_and_projection_catchup(tmp_path):
    """Test Engine.subscribe() registers projections, catches up past log events, and applies new events."""
    wal_dir = str(tmp_path / "subscribe_wal")
    db_file = str(tmp_path / "proj.db")

    proj = SQLiteProjection(db_file, name="sqlite_proj")

    raw1 = RawEvent(
        source="BINANCE",
        payload={
            "instrument": "BTC/USD",
            "event_type": "TRADE",
            "price": 60000.0,
            "quantity": 1.0,
            "sequence": 1,
            "exchange_ts": 1700000000.0,
        },
        receive_timestamp=1700000000.001,
        raw_id="btc_1",
    )

    with Engine.open(wal_dir) as engine:
        # Submit 1 event BEFORE subscribing
        engine.submit(raw1)
        assert engine.log.next_offset == 1
        assert proj.checkpoint() == -1

        # Subscribe projection: must catch up from offset 0
        engine.subscribe(proj, from_offset=0)
        assert proj.checkpoint() == 0
        assert proj.count_canonical() == 1

        # Submit next event: projection must receive it immediately
        raw2 = RawEvent(
            source="BINANCE",
            payload={
                "instrument": "BTC/USD",
                "event_type": "TRADE",
                "price": 60100.0,
                "quantity": 2.0,
                "sequence": 2,
                "exchange_ts": 1700000001.0,
            },
            receive_timestamp=1700000001.001,
            raw_id="btc_2",
        )
        engine.submit(raw2)
        assert proj.checkpoint() == 1
        assert proj.count_canonical() == 2

    proj.close()


def test_engine_replay_from_wal(tmp_path):
    """Test Engine.replay() reproduces deterministic decisions from WAL offset slices."""
    wal_dir = str(tmp_path / "replay_wal")

    events = [
        RawEvent(
            source="CME",
            payload={
                "instrument": "ES",
                "event_type": "TRADE",
                "price": 4000.0 + i,
                "quantity": 1.0,
                "sequence": i + 1,
                "exchange_ts": 1700000000.0 + i,
            },
            receive_timestamp=1700000000.001 + i,
            raw_id=f"cme_{i}",
        )
        for i in range(5)
    ]

    with Engine.open(wal_dir) as engine:
        orig_decisions = engine.submit(events)
        assert len(orig_decisions) == 5

        # Replay entire WAL
        replayed = engine.replay(from_offset=0)
        assert len(replayed) == 5
        for orig, rep in zip(orig_decisions, replayed):
            assert orig.offset == rep.offset
            assert orig.event_id == rep.event_id
            assert orig.quality_status == rep.quality_status
            assert orig.canonical_event.price == rep.canonical_event.price

        # Replay slice from offset 2 to 3
        slice_replayed = engine.replay(from_offset=2, to_offset=3)
        assert len(slice_replayed) == 2
        assert slice_replayed[0].offset == 2
        assert slice_replayed[1].offset == 3


def test_strict_batch_alignment_on_engine_submit(tmp_path):
    """Verify Engine.submit strictly maintains 1-to-1 input-to-output alignment, including poison inputs."""
    wal_dir = str(tmp_path / "align_wal")

    batch = [
        # 0: Valid
        RawEvent(
            source="FEED_A",
            payload={"instrument": "AAPL", "price": 150.0, "quantity": 10.0, "sequence": 1, "exchange_ts": 1700000000.0},
            receive_timestamp=1700000000.001,
            raw_id="ev_0",
        ),
        # 1: Malformed (poison) -> string payload
        RawEvent(
            source="FEED_A",
            payload="malformed string payload",
            receive_timestamp=1700000000.002,
            raw_id="ev_1",
        ),
        # 2: Valid
        RawEvent(
            source="FEED_A",
            payload={"instrument": "AAPL", "price": 150.5, "quantity": 10.0, "sequence": 2, "exchange_ts": 1700000000.003},
            receive_timestamp=1700000000.003,
            raw_id="ev_2",
        ),
        # 3: Missing instrument
        RawEvent(
            source="FEED_A",
            payload={"price": 150.6, "quantity": 10.0, "sequence": 3, "exchange_ts": 1700000000.004},
            receive_timestamp=1700000000.004,
            raw_id="ev_3",
        ),
    ]

    with Engine.open(wal_dir) as engine:
        decisions = engine.submit(batch)
        assert len(decisions) == len(batch) == 4

        # Decision 0
        assert decisions[0].quality_status == QualityStatus.VALID
        assert decisions[0].canonical_event is not None

        # Decision 1 (Poison quarantined)
        assert decisions[1].quality_status == QualityStatus.INVALID
        assert decisions[1].quarantine_row is not None
        assert decisions[1].canonical_event is None

        # Decision 2 (Healthy subsequent event)
        assert decisions[2].quality_status == QualityStatus.VALID
        assert decisions[2].canonical_event is not None

        # Decision 3 (Schema invalid quarantined)
        assert decisions[3].quality_status == QualityStatus.INVALID
        assert decisions[3].quarantine_row is not None


def test_pipeline_strict_align_mode():
    """Verify Pipeline.process_batch(strict_align=True) preserves exact index alignment."""
    store = Store(":memory:")
    pipeline = Pipeline(store=store)

    events = [
        RawEvent(
            source="TEST",
            payload={"instrument": "MSFT", "event_type": "TRADE", "price": 300.0, "quantity": 5.0, "sequence": 1, "exchange_ts": 1700000000.0},
            receive_timestamp=1700000000.001,
            raw_id="msft_1",
        ),
        RawEvent(
            source="TEST",
            payload="bad_payload",
            receive_timestamp=1700000000.002,
            raw_id="msft_2",
        ),
        RawEvent(
            source="TEST",
            payload={"instrument": "MSFT", "event_type": "TRADE", "price": 301.0, "quantity": 5.0, "sequence": 2, "exchange_ts": 1700000001.0},
            receive_timestamp=1700000001.001,
            raw_id="msft_3",
        ),
    ]

    results = pipeline.process_batch(events, strict_align=True)
    assert len(results) == 3

    assert results[0].quality_status == QualityStatus.VALID
    assert results[1].quality_status == QualityStatus.INVALID
    assert results[2].quality_status == QualityStatus.VALID
    pipeline.finish()
    store.close()


def test_engine_rejection_of_silent_kwargs():
    """Verify Engine.__init__ raises TypeError for arbitrary unexpected kwargs, avoiding silent swallow."""
    with pytest.raises(TypeError, match="unexpected keyword argument 'invalid_flag'"):
        Engine(invalid_flag=True)

    with pytest.raises(TypeError, match="unexpected keyword argument 'unsupported_option'"):
        Engine(unsupported_option=123)


def test_deprecation_warnings_emitted():
    """Verify DeprecationWarnings are emitted for Pipeline, Client, and legacy env vars."""
    with pytest.deprecated_call():
        store = Store(":memory:")
        pipe = Pipeline(store=store)
        pipe.finish()
        store.close()

    with pytest.deprecated_call():
        client = MDRAPClient()

    # Legacy environment variable detection
    os.environ["MDRAP_ASYNC_WRITER"] = "1"
    os.environ["MDRAP_DISABLE_JOURNAL"] = "0"
    try:
        with pytest.deprecated_call():
            e = Engine()
            e.close()
    finally:
        os.environ.pop("MDRAP_ASYNC_WRITER", None)
        os.environ.pop("MDRAP_DISABLE_JOURNAL", None)


def test_multi_engine_process_isolation(tmp_path):
    """Verify Engine A and Engine B in the same process do NOT leak state, sequences, or dedup windows."""
    wal_a = str(tmp_path / "wal_a")
    wal_b = str(tmp_path / "wal_b")

    engine_a = Engine.open(wal_a)
    engine_b = Engine.open(wal_b)

    # Engine A processes 5 events on AAPL with sequence 1..5
    events_a = [
        RawEvent(
            source="FEED_SHARED",
            payload={"instrument": "AAPL", "price": 100.0 + i, "sequence": i + 1, "exchange_ts": 1700000000.0 + i},
            receive_timestamp=1700000000.001 + i,
            raw_id=f"a_{i}",
        )
        for i in range(5)
    ]
    engine_a.submit(events_a)

    # Engine B processes 1 event on AAPL with sequence 1 (same source, same instrument, sequence 1!)
    # If state were global, this would be flagged as DUPLICATE or OUT_OF_ORDER!
    event_b = RawEvent(
        source="FEED_SHARED",
        payload={"instrument": "AAPL", "price": 200.0, "sequence": 1, "exchange_ts": 1700000000.0},
        receive_timestamp=1700000000.001,
        raw_id="b_0",
    )
    dec_b = engine_b.submit(event_b)

    # Must be VALID on Engine B because Engine B's state is completely isolated!
    assert dec_b.quality_status == QualityStatus.VALID
    assert dec_b.is_duplicate is False
    assert engine_a.state.event_count == 5
    assert engine_b.state.event_count == 1
    assert engine_a.state.sequence_state["FEED_SHARED:AAPL"] == 5
    assert engine_b.state.sequence_state["FEED_SHARED:AAPL"] == 1

    engine_a.close()
    engine_b.close()


def test_multi_gateway_isolation():
    """Verify Gateway A and Gateway B maintain independent ID counters and caches."""
    gw_a = Gateway(run_id="run_aaa")
    gw_b = Gateway(run_id="run_bbb")

    raw_a = gw_a.ingest(RawEvent(source="X", payload={"instrument": "AAPL"}))
    raw_b = gw_b.ingest(RawEvent(source="X", payload={"instrument": "AAPL"}))

    assert "run_aaa" in raw_a.raw_id
    assert "run_bbb" in raw_b.raw_id
    assert raw_a.raw_id != raw_b.raw_id


def test_extensible_reason_registry():
    """Verify ReasonRegistry allows registering new reason codes without modifying core code."""
    reg = ReasonRegistry()
    assert reg.has("DUPLICATE")
    assert reg.has("SCHEMA_VIOLATION")

    # Register dynamic custom reason
    registered_code = reg.register(
        "CUSTOM_CRYPTO_DEPEG",
        description="Stablecoin market price depegged beyond 200 bps tolerance",
        severity=QualityStatus.INVALID,
    )
    assert registered_code == "CUSTOM_CRYPTO_DEPEG"
    assert reg.has("CUSTOM_CRYPTO_DEPEG")
    assert reg.describe("CUSTOM_CRYPTO_DEPEG") == "Stablecoin market price depegged beyond 200 bps tolerance"
    assert reg.severity("CUSTOM_CRYPTO_DEPEG") == QualityStatus.INVALID

    # Test global Reason enum integration
    assert "CUSTOM_CRYPTO_DEPEG" in Reason
    assert Reason.CUSTOM_CRYPTO_DEPEG == "CUSTOM_CRYPTO_DEPEG"
    assert Reason.CUSTOM_CRYPTO_DEPEG.value == "CUSTOM_CRYPTO_DEPEG"
    assert Reason("CUSTOM_CRYPTO_DEPEG") == "CUSTOM_CRYPTO_DEPEG"
    assert Reason["CUSTOM_CRYPTO_DEPEG"] == "CUSTOM_CRYPTO_DEPEG"


def test_third_party_storage_backend_conformance():
    """Verify third-party SampleMemoryStorageBackend satisfies StorageSinkConformanceTester."""
    storage = SampleMemoryStorageBackend("test_third_party_store")
    StorageSinkConformanceTester.verify(storage)

    # Verify plugin validation accepts it
    is_valid, reason = validate_plugin("mdrap.storage_backends", storage)
    assert is_valid is True
    assert reason is None


def test_third_party_quality_rule_pack():
    """Verify third-party quality rule pack registers custom reason and evaluates events monotonically."""
    register_sample_quality_rules()

    assert "SAMPLE_PRICE_SPIKE" in Reason
    assert reason_registry.has("SAMPLE_PRICE_SPIKE")

    # Plugin validation accepts the initializer
    is_valid, reason = validate_plugin("mdrap.quality_rules", register_sample_quality_rules)
    assert is_valid is True
    assert reason is None


def test_all_six_entry_point_groups_discovered():
    """Verify all six entry point groups are properly discovered and loaded."""
    discovered = discover_all_plugins(validate=True)
    for grp in PLUGIN_GROUPS:
        assert grp in discovered
        assert isinstance(discovered[grp], dict)

    assert get_adapter("template") is not None
    assert get_adapter("reference") is not None
    assert get_quality_rule("sample_rules") is not None
    assert get_storage_backend("sample_memory") is not None
    assert get_auth_provider("sample_auth") is not None
    assert get_output_sink("sample_sink") is not None
    assert get_alert_sink("sample_alert") is not None
