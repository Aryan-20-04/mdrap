"""Adversarial reproduction test for P0-A: Crash during catch-up and restart verification."""

import os
import shutil
import sqlite3
import tempfile
import time

from mdrap import Engine, EngineConfig, RawEvent
from mdrap.projection import SQLiteProjection


def _make_event(
    i: int,
    source: str = "FEED_A",
    inst: str = "AAPL",
    base_ts: float = 1_700_000_000.0,
) -> RawEvent:
    return RawEvent(
        source=source,
        payload={
            "instrument": inst,
            "event_type": "QUOTE",
            "bid": 100.0 + (i % 10) * 0.1,
            "ask": 100.1 + (i % 10) * 0.1,
            "exchange_ts": base_ts + i * 0.001,
            "sequence": i,
        },
        receive_timestamp=base_ts + i * 0.001,
        raw_id=f"raw_{source}_{i}",
    )


def test_crash_during_catchup_and_restart():
    temp_dir = tempfile.mkdtemp(prefix="mdrap_p0_crash_")
    db_path = os.path.join(temp_dir, "projection.db")
    try:
        cfg = EngineConfig(
            wal_dir=temp_dir,
            snapshot_cadence_events=10000,
            fsync_policy="never",
        )
        engine = Engine(cfg)
        proj = SQLiteProjection(db_path=db_path, name="crash_test")
        engine.subscribe(proj)

        # Ingest 300 events
        print("[*] Ingesting initial 300 events...")
        for i in range(1, 301):
            engine.submit(_make_event(i))

        assert proj.checkpoint() == 299
        print(f"[+] Initial checkpoint: {proj.checkpoint()}")

        # Unsubscribe projection and ingest up to offset 1,500
        with engine._lock:
            engine._projections.remove(proj)

        print("[*] Ingesting up to offset 1500 with lagging projection...")
        for i in range(301, 1501):
            engine.submit(_make_event(i))

        wal_head = engine.log.next_offset
        assert wal_head == 1500
        print(f"[+] WAL head at offset {wal_head}")

        # Re-attach projection
        with engine._lock:
            engine._projections.append(proj)

        # Partially catch up: manually apply a batch of 300 events (offsets 300 to 599)
        # to simulate partial catch-up before an abrupt crash
        batch, last_off = engine._replay_projection_batch(300, max_batch_size=300)
        proj.apply(batch, last_off)
        print(f"[+] Mid-catchup checkpoint before crash: {proj.checkpoint()}")
        assert proj.checkpoint() == 599

        # Simulate abrupt crash: close DB connection and delete engine without normal shutdown
        proj.close()
        del engine
        import gc; gc.collect()

        print("[*] Simulated crash complete. Now restarting Engine on existing storage...")

        # Restart engine on existing WAL and DB
        engine2 = Engine(cfg)
        proj2 = SQLiteProjection(db_path=db_path, name="crash_test")

        # Verify initial state on restart BEFORE subscribe
        restarted_cp = proj2.checkpoint()
        print(f"[+] Post-restart projection checkpoint before subscribe: {restarted_cp}")
        assert restarted_cp == 599, f"Expected 599, got {restarted_cp}"

        # Now subscribe to trigger automatic catch-up
        engine2.subscribe(proj2)

        # Trigger recovery
        engine2.recover_projections()

        final_cp = proj2.checkpoint()
        total_rows = proj2.count_canonical()
        print(f"[+] Post-recovery final checkpoint: {final_cp}, Total rows: {total_rows}")

        assert final_cp == 1499, f"Expected final checkpoint 1499, got {final_cp}"
        assert total_rows == 1500, f"Expected total rows 1500, got {total_rows}"

        # Invariant check: verify all canonical sequence numbers are strictly contiguous from 1 to 1500
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        rows = cur.execute("SELECT sequence_number FROM canonical_events ORDER BY sequence_number ASC").fetchall()
        seqs = [r[0] for r in rows]
        conn.close()

        assert seqs == list(range(1, 1501)), f"Sequence discontinuity detected! Length {len(seqs)}, expected 1500"

        print("[SUCCESS] Zero skips, zero duplicates, strictly contiguous recovery verified.")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    test_crash_during_catchup_and_restart()
