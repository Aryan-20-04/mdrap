"""Adversarial reproduction test for P0-A: Concurrent Ingestion during Recovery."""

import os
import shutil
import tempfile
import threading
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


def test_concurrent_ingestion_during_recovery():
    temp_dir = tempfile.mkdtemp(prefix="mdrap_p0a_")
    db_path = os.path.join(temp_dir, "projection.db")
    try:
        cfg = EngineConfig(
            wal_dir=temp_dir,
            snapshot_cadence_events=10000,
            fsync_policy="never",
        )
        engine = Engine(cfg)
        proj = SQLiteProjection(db_path=db_path, name="test_p0a")
        engine.subscribe(proj)

        # 1. Ingest initial batch of 500 events
        print("[*] Ingesting initial 500 events...")
        for i in range(1, 501):
            engine.submit(_make_event(i))

        assert proj.checkpoint() == 499, f"Expected cp 499, got {proj.checkpoint()}"
        print(f"[+] Initial 500 events ingested. Projection checkpoint: {proj.checkpoint()}")

        # 2. Simulate projection lagging behind:
        # Create an outage projection or simulate lag by removing from _projections and submitting 1500 more events
        with engine._lock:
            engine._projections.remove(proj)
        print("[*] Unregistered projection. Ingesting 1,500 events to WAL head...")
        for i in range(501, 2001):
            engine.submit(_make_event(i))

        wal_head = engine.log.next_offset
        print(f"[+] WAL head is now at offset {wal_head}. Projection checkpoint remains at {proj.checkpoint()}")

        # 3. Now start recovery while concurrent ingestion continues
        with engine._lock:
            engine._projections.append(proj)
        tracker = engine._get_or_create_projection_tracker(proj)
        tracker.max_pending = 50  # Low capacity to force queue overflow easily


        stop_concurrent = threading.Event()
        ingest_errors = []
        ingested_count = [0]

        def concurrent_writer():
            seq = 2001
            while not stop_concurrent.is_set():
                try:
                    engine.submit(_make_event(seq))
                    ingested_count[0] += 1
                    seq += 1
                    time.sleep(0.001)
                except Exception as e:
                    ingest_errors.append(e)

        writer_thread = threading.Thread(target=concurrent_writer, daemon=True)
        writer_thread.start()

        # Run recovery
        print("[*] Starting engine.recover_projections() concurrently with new writes...")
        res = engine.recover_projections()
        print(f"[+] Initial recover_projections() returned: {res}")

        # Let concurrent writer run for a moment
        time.sleep(0.2)
        stop_concurrent.set()
        writer_thread.join(timeout=2.0)

        final_wal_head = engine.log.next_offset
        print(f"[+] Ingestion stopped. Total WAL head: {final_wal_head}, Extra ingested: {ingested_count[0]}")
        assert not ingest_errors, f"Ingest errors: {ingest_errors}"

        # Now drain tracker
        print("[*] Draining projection tracker...")
        tracker.drain()
        engine.recover_projections()

        final_cp = proj.checkpoint()
        expected_cp = final_wal_head - 1
        print(f"[+] Final projection checkpoint: {final_cp}, Expected: {expected_cp}")

        # Verify no missing events and exact count
        row_count = proj.count_canonical()
        print(f"[+] Total rows in projection: {row_count}, Expected: {final_wal_head}")

        assert final_cp == expected_cp, f"Checkpoint mismatch: {final_cp} != {expected_cp}"
        assert row_count == final_wal_head, f"Row count mismatch: {row_count} != {final_wal_head}"

        print("[SUCCESS] Concurrent ingestion during recovery verified.")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    test_concurrent_ingestion_during_recovery()
