"""Phase 3 Roadmap Verification Suite.

Tests for:
- Task 3.1: Pipeline-over-Engine adapter (EnginePipeline & use_engine mode)
- Task 3.2: Shadow-run & diff verification (Pipeline vs Engine.submit(), Diff = 0)
- Task 3.3: MarketDataDaemon Engine migration & canary rollback
- Task 3.4: CLI 12 core commands verification
- Task 3.5: Config unification & YAML-to-TOML migration
"""

import os
import sys
import tempfile
import pytest

from mdrap.models import RawEvent, QualityStatus
from mdrap.simulator import FeedSimulator, SimulatorConfig
from mdrap.storage import Store
from mdrap.pipeline import Pipeline, EnginePipeline
from mdrap.engine import Engine
from mdrap.service import MarketDataDaemon
from mdrap.config_loader import migrate_yaml_to_toml, load_config, resolve_config


def test_3_1_engine_pipeline_adapter_implements_legacy_interface():
    """Task 3.1: Verify EnginePipeline acts as a drop-in adapter over Engine.submit()."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_adapter.db")
        store = Store(db_path)
        try:
            pipe = EnginePipeline(store=store)
            assert pipe.use_engine is True
            assert isinstance(pipe.engine, Engine)

            # Test process_one
            raw_valid = RawEvent(
                source="ALPHA",
                receive_timestamp=1700000000.0,
                payload={
                    "instrument": "AAPL",
                    "event_type": "TRADE",
                    "exchange_ts": 1700000000.0,
                    "sequence": 1,
                    "price": 150.0,
                    "quantity": 100.0,
                },
            )
            ev = pipe.process_one(raw_valid)
            assert ev is not None
            assert ev.instrument_id == "AAPL"
            assert ev.price == 150.0
            assert ev.quality_status == QualityStatus.VALID

            # Test process_batch
            raw_batch = [
                RawEvent(
                    source="ALPHA",
                    receive_timestamp=1700000000.1,
                    payload={
                        "instrument": "AAPL",
                        "event_type": "TRADE",
                        "exchange_ts": 1700000000.1,
                        "sequence": 2,
                        "price": 150.5,
                        "quantity": 50.0,
                    },
                ),
                RawEvent(
                    source="ALPHA",
                    receive_timestamp=1700000000.2,
                    payload={
                        "instrument": "AAPL",
                        "event_type": "TRADE",
                        "exchange_ts": 1700000000.2,
                        "sequence": 3,
                        "price": 150.6,
                        "quantity": 25.0,
                    },
                ),
            ]
            batch_res = pipe.process_batch(raw_batch)
            assert len(batch_res) == 2
            assert batch_res[0].price == 150.5
            assert batch_res[1].price == 150.6
        finally:
            pipe.close()
            store.close()


def test_3_2_shadow_run_diff_zero():
    """Task 3.2: Verify zero diff between legacy Pipeline and Engine.submit() across realistic fault stream."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_pipe = os.path.join(tmpdir, "pipe.db")
        db_eng = os.path.join(tmpdir, "eng.db")
        store_pipe = Store(db_pipe)
        store_eng = Store(db_eng)

        try:
            pipe = Pipeline(store=store_pipe)
            engine = Engine(clock=None)

            sim_cfg = SimulatorConfig(
                seed=42,
                num_events=500,
                duplicate_rate=0.02,
                price_anomaly_rate=0.02,
                crossed_quote_rate=0.02,
            )
            sim = FeedSimulator(sim_cfg)

            pipe_verdicts = []
            eng_verdicts = []

            for raw, _ in sim.generate():
                # Process via legacy Pipeline
                ev_pipe = pipe.process_one(raw)
                pipe_status = ev_pipe.quality_status if ev_pipe else QualityStatus.INVALID
                pipe_reasons = sorted(ev_pipe.reasons) if ev_pipe else ["SCHEMA_VIOLATION"]
                pipe_verdicts.append((pipe_status, pipe_reasons))

                # Process via Engine.submit()
                dec_eng = engine.submit(raw)
                eng_status = dec_eng.quality_status
                eng_reasons = sorted(dec_eng.reasons)
                eng_verdicts.append((eng_status, eng_reasons))

            # Verify Diff = 0 across all events
            assert len(pipe_verdicts) == len(eng_verdicts)
            diffs = []
            for idx, (p_v, e_v) in enumerate(zip(pipe_verdicts, eng_verdicts)):
                if p_v != e_v:
                    diffs.append((idx, p_v, e_v))

            assert len(diffs) == 0, f"Found {len(diffs)} diffs between Pipeline and Engine: {diffs[:5]}"
        finally:
            pipe.close()
            engine.close()
            store_pipe.close()
            store_eng.close()


def test_3_3_market_data_daemon_engine_canary_and_rollback():
    """Task 3.3: Test MarketDataDaemon Engine migration with canary flag and rollback capability."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_canary = os.path.join(tmpdir, "daemon_canary.db")
        db_rollback = os.path.join(tmpdir, "daemon_rollback.db")

        # 1. Canary: use_engine=True (default-on)
        dmn_canary = MarketDataDaemon(
            db_path=db_canary,
            enable_shm=False,
            use_engine=True,
        )
        assert dmn_canary.use_engine is True
        assert dmn_canary.engine is not None

        raw = RawEvent(
            source="ALPHA",
            receive_timestamp=1700000000.0,
            payload={
                "instrument": "AAPL",
                "event_type": "QUOTE",
                "exchange_ts": 1700000000.0,
                "sequence": 1,
                "bid": 180.0,
                "ask": 180.05,
            },
        )
        try:
            dmn_canary._process_and_broadcast(raw)
            assert dmn_canary._total_broadcast == 1
            bbo = dmn_canary.bbo.current_bbo("AAPL")
            assert bbo is not None
        finally:
            dmn_canary.close()

        # 2. Rollback: use_engine=False
        dmn_rollback = MarketDataDaemon(
            db_path=db_rollback,
            enable_shm=False,
            use_engine=False,
        )
        assert dmn_rollback.use_engine is False
        assert dmn_rollback.engine is None

        try:
            dmn_rollback._process_and_broadcast(raw)
            assert dmn_rollback._total_broadcast == 1
        finally:
            dmn_rollback.close()


def test_3_4_cli_core_twelve_commands():
    """Task 3.4: Verify all 12 core CLI commands are recognized and accessible."""
    from mdrap.cli import build_parser

    parser = build_parser()
    core_commands = [
        "serve",
        "ingest",
        "verify",
        "replay",
        "stats",
        "status",
        "export",
        "config",
        "bench",
        "quarantine",
        "keys",
        "doctor",
    ]
    for cmd in core_commands:
        # Check parser accepts the command without error
        args, _ = parser.parse_known_args([cmd])
        assert args is not None


def test_3_5_config_yaml_to_toml_migration():
    """Task 3.5: Verify config.yaml to mdrap.toml migration preserves effective settings."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yaml_path = os.path.join(tmpdir, "config.yaml")
        toml_path = os.path.join(tmpdir, "mdrap.toml")

        with open(yaml_path, "w", encoding="utf-8") as f:
            f.write(
                "quality:\n"
                "  staleness_threshold_s: 0.15\n"
                "  price_anomaly_stddev: 4.5\n"
                "  price_window: 60\n"
                "  dedup_cache_size: 100000\n"
            )

        migrated = migrate_yaml_to_toml(yaml_path, toml_path)
        assert os.path.exists(migrated)

        cfg = load_config(migrated)
        resolved, _ = resolve_config(cfg)
        assert resolved["staleness_threshold_s"] == 0.15
        assert resolved["price_anomaly_stddev"] == 4.5
        assert resolved["price_window"] == 60
        assert resolved["dedup_cache_size"] == 100000
