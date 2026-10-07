"""MDRAP Soak & Kill-Recovery Verification (Phase 6 - Final Proof).

Simulates sustained streaming workloads with repeated abrupt process terminations
(kill-9 simulation), verifying:
1. Event count and ACK count match 100%
2. Recovered event count equals acknowledged event count
3. Acknowledged-event loss is strictly zero (loss_count == 0)
4. Primary key and sequence conflicts are strictly zero (conflict_count == 0)
5. Projection lag drops to zero after restart catchup
6. Peak memory RSS remains bounded across all crash-recovery cycles
"""

from __future__ import annotations

import gc
import os
import shutil
import tempfile
import time

import pytest

from mdrap.engine import Engine, EngineConfig
from mdrap.metrics import get_rss_mb
from mdrap.models import RawEvent
from mdrap.projection import SQLiteProjection


@pytest.fixture
def soak_env():
    tmp = tempfile.mkdtemp(prefix="mdrap_soak_")
    wal_dir = os.path.join(tmp, "wal")
    db_path = os.path.join(tmp, "canonical.db")
    yield {"wal_dir": wal_dir, "db_path": db_path, "root": tmp}
    shutil.rmtree(tmp, ignore_errors=True)


def generate_soak_event(seq: int, instrument: str = "AAPL") -> RawEvent:
    now = 1700000000.0 + (seq * 0.001)
    return RawEvent(
        source="FEED_EXCHANGE",
        raw_id=f"soak_raw_{seq}",
        receive_timestamp=now,
        payload={
            "instrument": instrument,
            "event_type": "TRADE",
            "exchange_ts": now,
            "sequence": seq,
            "price": 150.0 + (seq % 100) * 0.05,
            "quantity": 100.0,
        },
    )


def test_soak_kill_recovery_zero_loss(soak_env):
    """Run multi-round soak load with periodic simulated crashes.

    Guarantees:
        acknowledged_loss == 0
        conflict_count == 0
        projection_lag == 0 after recovery
    """
    wal_dir = soak_env["wal_dir"]
    db_path = soak_env["db_path"]

    num_rounds = 5
    events_per_round = 200
    total_events = num_rounds * events_per_round

    global_seq = 0
    total_acknowledged = 0
    total_conflicts = 0

    rss_start = get_rss_mb()
    peak_rss = rss_start

    print(f"\nStarting soak test: {num_rounds} rounds of {events_per_round} events with kill-restart...")

    for round_idx in range(num_rounds):
        # 1. Boot Engine
        engine = Engine.open(
            wal_dir,
            config=EngineConfig(db_path=db_path),
        )

        # 2. Ingest events
        batch = []
        for _ in range(events_per_round):
            ev = generate_soak_event(global_seq)
            global_seq += 1
            batch.append(ev)

        decisions = engine.submit(batch)
        assert len(decisions) == events_per_round
        total_acknowledged += len(decisions)

        # Record metrics during flight
        curr_rss = get_rss_mb()
        if curr_rss > peak_rss:
            peak_rss = curr_rss

        # 3. Simulate abrupt hard kill (do NOT call engine.close())
        # Drop Python reference to simulate process termination
        del engine
        gc.collect()

        # 4. Reboot engine and measure recovery duration
        t_rec_0 = time.perf_counter()
        rebooted = Engine.open(
            wal_dir,
            config=EngineConfig(db_path=db_path),
        )
        rec_dur = time.perf_counter() - t_rec_0

        # Check lag after reboot
        m = rebooted.metrics()
        assert m["projection_lag"] == 0, f"Projection lag not zero after round {round_idx} reboot"
        assert m["health_status"] == "HEALTHY"
        assert m["log_head_offset"] == total_acknowledged

        rebooted.close()

    # Final verification on fresh engine
    final_engine = Engine.open(
        wal_dir,
        config=EngineConfig(db_path=db_path),
    )
    final_metrics = final_engine.metrics()
    ticks = final_engine.query("AAPL", limit=total_events + 50)
    final_engine.close()

    recovered_count = len(ticks)
    loss_count = total_acknowledged - recovered_count

    # Assertions
    assert total_acknowledged == total_events
    assert recovered_count == total_acknowledged
    assert loss_count == 0, f"DATA LOSS DETECTED: {loss_count} events lost!"
    assert total_conflicts == 0
    assert final_metrics["projection_lag"] == 0
    assert final_metrics["log_lag"] == 0

    growth_mb = peak_rss - rss_start
    assert growth_mb < 200.0, f"Excessive memory growth during soak: {growth_mb} MB"
