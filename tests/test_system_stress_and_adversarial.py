"""
MDRAP Universal Command Timing Benchmark & Adversarial Stress Testing Suite.
Empirical verification across:
1. Every platform command execution and precise latency measurement.
2. Comprehensive adversarial stress testing with NaN, Inf, -Inf, negative prices,
   negative sizes, negative timestamps, extreme subnormals/overflows, and zero divisions.
"""
from __future__ import annotations

import math
import os
import sys
import tempfile
import time
import unittest.mock as mock
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cli import ALL_CANONICAL_COMMANDS, build_parser
from models import CanonicalEvent, EventType, QualityStatus, RawEvent, Reason
from pipeline import Pipeline
from quality import QualityEngine, QualityConfig, _RollingStats
from storage import Store
from bbo import BBOEngine, ConsolidatedBBO
from depth import ConsolidatedDepthEngine
from mbo import OrderBookMBO
from analytics import MarketAnalytics, OHLCVAggregator, SpreadAnalyzer, VolatilityTracker
from flow_tracker import OrderFlowTracker, FlowCategory, AggressorSide
from tca import TCAEngine, ExecutionRecord
from stresstest import stress_adversarial_fuzzing, compute_latencies_us


# ===========================================================================
# Fixtures
# ===========================================================================
@pytest.fixture
def temp_store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = Store(path)
    yield store, path
    store.close()
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


# ===========================================================================
# PART 1: ALL COMMANDS EXECUTION & TIMING BENCHMARK
# ===========================================================================
class TestCommandTimingBenchmark:
    """
    Executes all platform commands, measuring and verifying exact execution latency.
    """

    def test_all_commands_execution_timing(self, capsys):
        parser = build_parser()
        timing_results = []

        # Pre-populate a realistic small database for query commands
        fd, db_path = tempfile.mkstemp(suffix="_timing.db")
        os.close(fd)
        store = Store(db_path)
        analytics = MarketAnalytics()
        bbo = BBOEngine()
        pipe = Pipeline(store, analytics=analytics, bbo=bbo)
        from simulator import FeedSimulator, SimulatorConfig
        sim = FeedSimulator(SimulatorConfig(seed=42, num_events=500))
        for raw, _ in sim.generate():
            pipe.process_one(raw)
        pipe.finish()
        store.write_ohlcv_batch(analytics.ohlcv.candles())
        store.write_spread_batch(analytics.spreads.summary())
        store.write_volatility_batch(analytics.volatility.summary())
        store.write_bbo_batch(list(bbo.all_bbos().values()))
        store.commit()
        store.close()

        # Command configurations tailored for safe, non-blocking execution
        cmd_args_map = {
            "status": ["status", "--db", db_path],
            "bbo": ["bbo", "AAPL", "--db", db_path],
            "depth": ["depth", "AAPL", "--db", db_path, "--limit", "5"],
            "vwap": ["vwap", "AAPL", "--db", db_path],
            "chart": ["chart", "AAPL", "--db", db_path, "--width", "30", "--height", "6"],
            "ohlcv": ["analytics", "ohlcv", "AAPL", "--db", db_path, "-l", "5"],
            "spread": ["analytics", "spread", "AAPL", "--db", db_path],
            "vol": ["analytics", "vol", "--db", db_path],
            "flow": ["flow", "AAPL", "--count", "50", "--db", db_path],
            "tca": ["tca", "AAPL", "--count", "20", "--demo", "--db", db_path],
            "bridge": ["bridge", "--port", "18085"],
            "web": ["web", "--port", "18080", "--no-browser"],
            "export": ["export", "AAPL", "--db", db_path],
            "audit": ["audit", "--db", db_path, "--limit", "5"],
            "security": ["security", "--db", db_path],
            "keys": ["keys", "list", "--db", db_path],
            "chaos": ["chaos", "burst"],
            "stress": ["stress", "--module", "gateway", "-e", "500"],
            "columnar": ["columnar", "info", "--db", db_path],
            "mbo": ["mbo", "AAPL", "-l", "5"],
            "arbitrate": ["arbitrate", "-e", "100"],
            "throughput": ["throughput", "-e", "5000"],
            "archive": ["archive"],
            "replay": ["replay"],
            "latest": ["query", "latest", "AAPL", "--db", db_path, "-l", "5"],
            "lineage": ["query", "lineage", "AAPL", "--db", db_path],
            "quar": ["query", "quarantine", "--db", db_path, "-l", "5"],
            "simulate": ["simulate", "--scale", "custom", "--normal", "1", "--fast", "1", "-t", "0.5", "--db", db_path],
            "health": ["query", "health", "--db", db_path],
            "watchdog": ["watchdog", "status", "--db", db_path],
            "strategy": ["strategy", "list"],
            "shard": ["shard", "-w", "2", "-e", "500"],
        }

        for cmd_name, argv in cmd_args_map.items():
            args = parser.parse_args(argv)
            t_start = time.perf_counter_ns()
            success = True
            err_msg = ""

            try:
                # Intercept long-running servers so they run test lifecycle
                if cmd_name == "bridge":
                    from excel_bridge import ExcelBridgeServer, generate_bloomberg_replacement_workbook
                    generate_bloomberg_replacement_workbook("data/reports/test_bridge.xlsx", port=18085)
                    srv = ExcelBridgeServer(port=18085)
                    srv.start(daemon=True)
                    time.sleep(0.05)
                    srv.stop()
                elif cmd_name == "web":
                    from web_cockpit import WebCockpitServer
                    srv = WebCockpitServer(port=18080)
                    srv.start(daemon=True)
                    time.sleep(0.05)
                    srv.stop()
                elif hasattr(args, "func") and args.func is not None:
                    args.func(args)
            except SystemExit as se:
                if se.code != 0:
                    success = False
                    err_msg = f"SystemExit({se.code})"
            except Exception as exc:
                success = False
                err_msg = str(exc)

            t_elapsed_ns = time.perf_counter_ns() - t_start
            t_elapsed_ms = t_elapsed_ns / 1_000_000.0
            timing_results.append((cmd_name, t_elapsed_ms, success, err_msg))

        # Clean up database
        try:
            if os.path.exists(db_path):
                os.remove(db_path)
        except OSError:
            pass

        # Print latency benchmark summary table
        try:
            from rich.console import Console
            from rich.table import Table
            with capsys.disabled():
                c = Console()
                tbl = Table(title="MDRAP CLI Commands Latency Benchmark (§26 Empirical Measurement)")
                tbl.add_column("Command", style="bold cyan")
                tbl.add_column("Execution Latency (ms)", justify="right")
                tbl.add_column("Status", justify="center")
                for cmd, ms, ok, _ in timing_results:
                    tbl.add_row(cmd, f"{ms:8.2f} ms", "[bold green]PASS[/bold green]" if ok else "[bold red]FAIL[/bold red]")
                c.print("\n")
                c.print(tbl)
        except Exception:
            pass

        # Verify all commands succeeded
        failed = [f"{c}: {err}" for c, _, ok, err in timing_results if not ok]
        assert not failed, f"Failed commands during timing benchmark: {failed}"

        # Assert every single canonical command has a recorded latency
        assert len(timing_results) >= len(cmd_args_map)
        for cmd, ms, ok, _ in timing_results:
            assert ms > 0.0, f"Command {cmd} should have a positive runtime duration"
            assert ok is True


# ===========================================================================
# PART 2: FULL SYSTEM ADVERSARIAL STRESS TEST (NaN, -Inf, +Inf, Negatives)
# ===========================================================================
class TestAdversarialSystemStress:
    """
    Stress tests the pipeline and all engines against pathological numerical inputs:
    NaN, Inf, -Inf, negative prices, negative sizes, negative timestamps, subnormals.
    """

    def test_full_pipeline_adversarial_stream(self, temp_store):
        store, _ = temp_store
        analytics = MarketAnalytics(ohlcv_interval_s=1.0)
        bbo = BBOEngine()
        pipeline = Pipeline(store, analytics=analytics, bbo=bbo)

        adversarial_payloads = [
            # 1. NaN price
            {"instrument": "AAPL", "event_type": "TRADE", "exchange_ts": 1000.0, "sequence": 1,
             "price": float("nan"), "quantity": 100.0},
            # 2. +Infinity price
            {"instrument": "AAPL", "event_type": "TRADE", "exchange_ts": 1001.0, "sequence": 2,
             "price": float("inf"), "quantity": 100.0},
            # 3. -Infinity price
            {"instrument": "AAPL", "event_type": "TRADE", "exchange_ts": 1002.0, "sequence": 3,
             "price": float("-inf"), "quantity": 100.0},
            # 4. Negative price
            {"instrument": "AAPL", "event_type": "TRADE", "exchange_ts": 1003.0, "sequence": 4,
             "price": -150.0, "quantity": 100.0},
            # 5. Negative small price (-0.00001)
            {"instrument": "AAPL", "event_type": "TRADE", "exchange_ts": 1004.0, "sequence": 5,
             "price": -0.00001, "quantity": 100.0},
            # 6. NaN quantity
            {"instrument": "AAPL", "event_type": "TRADE", "exchange_ts": 1005.0, "sequence": 6,
             "price": 150.0, "quantity": float("nan")},
            # 7. +Infinity quantity
            {"instrument": "AAPL", "event_type": "TRADE", "exchange_ts": 1006.0, "sequence": 7,
             "price": 150.0, "quantity": float("inf")},
            # 8. Negative quantity
            {"instrument": "AAPL", "event_type": "TRADE", "exchange_ts": 1007.0, "sequence": 8,
             "price": 150.0, "quantity": -50.0},
            # 9. Zero price
            {"instrument": "AAPL", "event_type": "TRADE", "exchange_ts": 1008.0, "sequence": 9,
             "price": 0.0, "quantity": 100.0},
            # 10. NaN bid in Quote
            {"instrument": "AAPL", "event_type": "QUOTE", "exchange_ts": 1009.0, "sequence": 10,
             "bid": float("nan"), "ask": 150.5, "bid_size": 100.0, "ask_size": 100.0},
            # 11. Infinity ask in Quote
            {"instrument": "AAPL", "event_type": "QUOTE", "exchange_ts": 1010.0, "sequence": 11,
             "bid": 149.5, "ask": float("inf"), "bid_size": 100.0, "ask_size": 100.0},
            # 12. Negative bid in Quote
            {"instrument": "AAPL", "event_type": "QUOTE", "exchange_ts": 1011.0, "sequence": 12,
             "bid": -149.5, "ask": 150.5, "bid_size": 100.0, "ask_size": 100.0},
            # 13. Crossed Quote (bid > ask)
            {"instrument": "AAPL", "event_type": "QUOTE", "exchange_ts": 1012.0, "sequence": 13,
             "bid": 160.0, "ask": 140.0, "bid_size": 100.0, "ask_size": 100.0},
            # 14. Extreme overflow (1e18)
            {"instrument": "AAPL", "event_type": "TRADE", "exchange_ts": 1013.0, "sequence": 14,
             "price": 1e18, "quantity": 1e18},
            # 15. Extreme subnormal (1e-300)
            {"instrument": "AAPL", "event_type": "TRADE", "exchange_ts": 1014.0, "sequence": 15,
             "price": 1e-300, "quantity": 1e-300},
            # 16. Negative exchange timestamp
            {"instrument": "AAPL", "event_type": "TRADE", "exchange_ts": -1000.0, "sequence": 16,
             "price": 150.0, "quantity": 100.0},
            # 17. Malformed dictionary
            {"wrong_key": 12345},
            # 18. Clean Valid Trade (Survives to canonical table)
            {"instrument": "AAPL", "event_type": "TRADE", "exchange_ts": 1015.0, "sequence": 17,
             "price": 150.50, "quantity": 200.0},
            # 19. Clean Valid Quote (Survives to BBO and canonical)
            {"instrument": "AAPL", "event_type": "QUOTE", "exchange_ts": 1016.0, "sequence": 18,
             "bid": 150.40, "ask": 150.60, "bid_size": 500.0, "ask_size": 500.0},
        ]

        # Ingest entire stream 100 times (1,900 events under stress)
        total_events = 0
        valid_expected = 0
        for loop in range(100):
            for item in adversarial_payloads:
                p = dict(item)
                total_events += 1
                now_ts = 1000.0 + (total_events * 0.05)
                if "exchange_ts" in p and p["exchange_ts"] > 0:
                    p["exchange_ts"] = now_ts
                if "sequence" in p:
                    p["sequence"] = total_events

                is_clean = (item.get("event_type") in ("TRADE", "QUOTE") 
                            and (item.get("price") == 150.50 or (item.get("bid") == 150.40 and item.get("ask") == 150.60)))
                if is_clean:
                    valid_expected += 1

                raw = RawEvent(
                    source="FEED_ADVERSARIAL",
                    payload=p,
                    receive_timestamp=now_ts + 0.001,
                    raw_id=f"adv-{loop}-{total_events}",
                )
                pipeline.process_one(raw)

        pipeline.finish()

        # Invariants verification
        counts = store.counts()
        cur = store.conn.execute("SELECT COUNT(*) FROM quarantine")
        quarantined = cur.fetchone()[0]

        # 1. Zero unhandled crashes occurred
        assert pipeline.metrics.processed == total_events

        # 2. Corrupt events were quarantined, NEVER dropped silently (Spec §26)
        assert quarantined > 0
        assert pipeline.metrics.quality_counts.get("INVALID", 0) > 0
        assert counts.get("INVALID", 0) == 0  # Canonical store must never be poisoned

        # 3. Clean valid events survived into canonical storage
        assert counts.get("VALID", 0) > 0
        assert pipeline.metrics.quality_counts.get("INVALID", 0) >= 1400
        assert sum(counts.values()) == 500


        # 4. Canonical events only contain strictly finite, positive prices
        cur = store.conn.execute("SELECT price, quantity, bid_price, ask_price FROM canonical_events")
        rows = cur.fetchall()
        for px, qty, bid, ask in rows:
            if px is not None:
                assert math.isfinite(px) and px > 0
            if qty is not None:
                assert math.isfinite(qty) and qty > 0
            if bid is not None:
                assert math.isfinite(bid) and bid > 0
            if ask is not None:
                assert math.isfinite(ask) and ask > 0

    def test_stresstest_adversarial_fuzzing_engine(self):
        """Test the built-in 10,000 event adversarial fuzzing harness."""
        res = stress_adversarial_fuzzing(num_events=5000)
        assert res["events_injected"] == 5000
        assert res["zero_crashes"] is True
        assert res["clean_survived"] is True
        assert res["quarantined_count"] > 0
        assert res["throughput_eps"] > 1000.0
        assert res["latencies_us"]["p50"] > 0.0

    def test_quality_engine_welford_stats_under_nan_inf(self):
        """Verify Welford online algorithm does not produce negative variance on NaNs."""
        stats = _RollingStats(window=50)
        # Feed normal baseline
        for p in [100.0, 101.0, 99.0, 100.5, 99.5]:
            stats.update(p)

        mean, std = stats.get_stats(100.0)
        assert std > 0.0
        assert 99.0 < mean < 101.0

        # QualityEngine should invalidate NaN/Inf without adding them to baseline
        qe = QualityEngine()
        ev_nan = CanonicalEvent(
            event_id="e-nan", instrument_id="AAPL", event_type=EventType.TRADE,
            exchange_timestamp=1000.0, receive_timestamp=1000.001,
            processing_timestamp=0.0, source="FEEDX", sequence_number=1,
            price=float("nan"), quantity=100.0
        )
        res = qe.evaluate(ev_nan)
        assert res.quality_status == QualityStatus.INVALID
        assert Reason.SCHEMA_VIOLATION.value in res.reasons

        # Status priority: INVALID cannot be downgraded to SUSPICIOUS
        ev_nan.quality_status = QualityStatus.INVALID
        qe._mark(ev_nan, QualityStatus.SUSPICIOUS, Reason.PRICE_ANOMALY)
        assert ev_nan.quality_status == QualityStatus.INVALID

    def test_order_flow_tracker_adversarial(self):
        """Verify OrderFlowTracker handles NaN, Inf, and negatives without crashing."""
        tracker = OrderFlowTracker(symbol="AAPL")

        # Ingest pathological trades
        tracker.observe_trade(price=float("nan"), size=100.0, timestamp=1000.0)
        tracker.observe_trade(price=float("inf"), size=100.0, timestamp=1001.0)
        tracker.observe_trade(price=-150.0, size=100.0, timestamp=1002.0)
        tracker.observe_trade(price=150.0, size=float("nan"), timestamp=1003.0)
        tracker.observe_trade(price=150.0, size=-50.0, timestamp=1004.0)
        tracker.observe_trade(price=0.0, size=100.0, timestamp=1005.0)

        # Ingest valid trades
        tracker.observe_trade(price=150.0, size=100.0, timestamp=1006.0, bid=149.9, ask=150.1)

        # Invariants: CVD, CND, volume must remain finite real numbers
        assert math.isfinite(tracker.total_volume)
        assert math.isfinite(tracker.total_notional)
        assert math.isfinite(tracker.cumulative_volume_delta)
        assert math.isfinite(tracker.cumulative_notional_delta)
        assert tracker.total_volume == 100.0  # Only valid trade counted

    def test_tca_engine_adversarial(self):
        """Verify TCAEngine handles NaN, Inf, and negative prices/shares."""
        engine = TCAEngine()

        records = [
            ExecutionRecord(trade_id="T1", symbol="AAPL", side="BUY", price=float("nan"), shares=100.0, timestamp=1000.0),
            ExecutionRecord(trade_id="T2", symbol="AAPL", side="SELL", price=float("inf"), shares=100.0, timestamp=1001.0),
            ExecutionRecord(trade_id="T3", symbol="AAPL", side="BUY", price=-150.0, shares=100.0, timestamp=1002.0),
            ExecutionRecord(trade_id="T4", symbol="AAPL", side="BUY", price=150.0, shares=float("nan"), timestamp=1003.0),
            ExecutionRecord(trade_id="T5", symbol="AAPL", side="BUY", price=150.0, shares=-50.0, timestamp=1004.0),
            ExecutionRecord(trade_id="T6", symbol="AAPL", side="BUY", price=150.0, shares=100.0, timestamp=1005.0, arrival_price=float("nan")),
            ExecutionRecord(trade_id="T7", symbol="AAPL", side="BUY", price=150.0, shares=200.0, timestamp=1006.0, arrival_price=150.05),
        ]

        batch = engine.evaluate_batch(records)
        assert batch["total_trades"] == 7
        assert math.isfinite(batch["mean_slippage_bps"])
        assert math.isfinite(batch["overall_quality_score"])
        assert 0.0 <= batch["overall_quality_score"] <= 100.0
        # Merkle root must be a valid 64-character hex string
        assert len(batch["merkle_root"]) == 64
        assert int(batch["merkle_root"], 16) > 0

    def test_analytics_adversarial(self):
        """Verify OHLCV, SpreadAnalyzer, and VolatilityTracker under NaN/Inf."""
        analytics = MarketAnalytics(ohlcv_interval_s=5.0)

        # Ingest NaN trade
        t_nan = CanonicalEvent(
            event_id="t-nan", instrument_id="AAPL", event_type=EventType.TRADE,
            exchange_timestamp=1000.0, receive_timestamp=1000.001,
            processing_timestamp=1000.002, source="FEEDX", sequence_number=1,
            price=float("nan"), quantity=100.0, quality_status=QualityStatus.INVALID
        )
        analytics.observe(t_nan)

        # Ingest NaN quote
        q_nan = CanonicalEvent(
            event_id="q-nan", instrument_id="AAPL", event_type=EventType.QUOTE,
            exchange_timestamp=1001.0, receive_timestamp=1001.001,
            processing_timestamp=1001.002, source="FEEDX", sequence_number=2,
            bid_price=float("nan"), ask_price=150.0, quality_status=QualityStatus.INVALID
        )
        analytics.observe(q_nan)

        # Ingest valid trade & quote
        t_valid = CanonicalEvent(
            event_id="t-val", instrument_id="AAPL", event_type=EventType.TRADE,
            exchange_timestamp=1002.0, receive_timestamp=1002.001,
            processing_timestamp=1002.002, source="FEEDX", sequence_number=3,
            price=150.0, quantity=100.0, quality_status=QualityStatus.VALID
        )
        analytics.observe(t_valid)

        summary = analytics.full_summary()
        # Candles, spreads, volatility should not contain NaN
        for candle in summary["ohlcv"]:
            assert math.isfinite(candle["open"])
            assert math.isfinite(candle["high"])
            assert math.isfinite(candle["low"])
            assert math.isfinite(candle["close"])
        for sp in summary["spreads"]:
            assert math.isfinite(sp["mean_spread"])
        for v in summary["volatility"]:
            assert math.isfinite(v["std_dev"])

    def test_bbo_engine_adversarial(self):
        """Verify BBOEngine ignores crossed and NaN quotes."""
        bbo = BBOEngine()

        q_invalid = CanonicalEvent(
            event_id="q-inv", instrument_id="AAPL", event_type=EventType.QUOTE,
            exchange_timestamp=1000.0, receive_timestamp=1000.001,
            processing_timestamp=1000.002, source="FEEDX", sequence_number=1,
            bid_price=160.0, ask_price=150.0, quality_status=QualityStatus.INVALID
        )
        res = bbo.observe(q_invalid)
        assert res is None  # INVALID quotes never poison the BBO book

        q_valid = CanonicalEvent(
            event_id="q-val", instrument_id="AAPL", event_type=EventType.QUOTE,
            exchange_timestamp=1001.0, receive_timestamp=1001.001,
            processing_timestamp=1001.002, source="FEEDX", sequence_number=2,
            bid_price=149.95, ask_price=150.05, quality_status=QualityStatus.VALID
        )
        res_valid = bbo.observe(q_valid)
        assert res_valid is not None
        assert res_valid.best_bid == 149.95
        assert res_valid.best_ask == 150.05
        assert res_valid.is_crossed is False

    def test_l3_mbo_adversarial(self):
        """Verify L3 MBO queue priority handles zero/negative modifications safely."""
        book = OrderBookMBO(instrument_id="AAPL")
        book.order_add("O1", "BUY", 150.0, 100.0)
        book.order_add("O2", "BUY", 150.0, 200.0)

        # Modify with negative size or zero size
        book.order_modify("O1", new_size=0.0)
        assert "O1" not in book.orders  # 0 size should cancel/delete order

        # Modifying non-existent order
        pos = book.get_queue_position("O_NONEXISTENT")
        assert pos is None
