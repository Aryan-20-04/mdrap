"""
MDRAP Phase 9 End-to-End Pipeline & Forensic Historical Integrity Test Harness.

Validates:
1. End-to-End Pipeline Flow:
   Ingress -> IngestLog WAL -> Gateway Normalization -> Quality Evaluation ->
   Multi-Feed Reconciliation -> SQLite WAL Storage -> Async Fan-Out Broadcast -> Consumer Read.
2. Quality Engine Enforcement:
   Valid ticks vs Anomalous/Crossed quotes vs Duplicates properly quarantined and tagged.
3. Forensic Historical Integrity:
   - Bounded-memory CRC32 frame checksum verification
   - Cryptographic SHA-256 Merkle root computation
   - Corrupted bit-flip payload detection (tamper-proofing)
   - Truncated frame / torn-tail detection
   - SQLite PRAGMA integrity_check verification
"""

import json
import os
import shutil
import sys
import time
from typing import Any, Dict, List

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
if os.path.join(_REPO_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))

from mdrap.async_fanout import AsyncFanoutManager
from mdrap.historical_verifier import HistoricalVerifier
from mdrap.ingestlog import IngestLog
from mdrap.models import EventType, QualityStatus, RawEvent
from mdrap.pipeline import Pipeline
from mdrap.quality import QualityEngine
from mdrap.reconciliation import ReliabilityTracker
from mdrap.storage import Store


def run_e2e_campaign():
    print("=== MDRAP Phase 9 End-to-End Pipeline & Forensic Integrity Verification ===")
    
    test_dir = os.path.join(_REPO_ROOT, "audit", "phase9", "data_e2e")
    if os.path.exists(test_dir):
        shutil.rmtree(test_dir, ignore_errors=True)
    os.makedirs(test_dir, exist_ok=True)
    
    db_path = os.path.join(test_dir, "market_data_uat.db")
    wal_dir = os.path.join(test_dir, "wal_logs")
    os.makedirs(wal_dir, exist_ok=True)

    # 1. Initialize Pipeline Infrastructure
    store = Store(db_path)
    ingest_log = IngestLog(wal_dir, fsync_policy="always")
    quality_engine = QualityEngine()
    reliability = ReliabilityTracker()
    fanout = AsyncFanoutManager(max_buffer_per_client=5000)
    client_id = "uat_algo_subscriber_01"
    fanout.register_consumer(client_id)

    pipeline = Pipeline(
        store=store,
        quality=quality_engine,
        reliability=reliability,
        async_writer=False,  # Synchronous persistence for deterministic validation
    )

    # 2. Ingest Multi-Feed Workload
    total_events = 1200
    valid_count = 0
    quarantine_count = 0
    now = time.time()
    
    seq_map = {}
    print(f"Streaming {total_events} heterogeneous market events across 3 venues...")
    t0_pipe = time.perf_counter()

    for i in range(total_events):
        sym = "AAPL" if i % 3 == 0 else ("MSFT" if i % 3 == 1 else "GOOG")
        source = "FEED_A" if i % 2 == 0 else "FEED_B"
        key = (source, sym)
        seq_map[key] = seq_map.get(key, 0) + 1
        seq = seq_map[key]
        
        # Inject deliberate anomalies for forensic verification
        if i == 50:
            # Negative price anomaly
            payload = {
                "instrument": sym,
                "event_type": "TRADE",
                "price": -150.0,
                "quantity": 100,
                "exchange_ts": now + (i * 0.001),
                "sequence": seq,
            }
        elif i == 150:
            # Crossed quote anomaly (bid > ask)
            payload = {
                "instrument": sym,
                "event_type": "QUOTE",
                "bid": 250.0,
                "ask": 240.0,
                "bid_size": 10,
                "ask_size": 10,
                "exchange_ts": now + (i * 0.001),
                "sequence": seq,
            }
        elif i == 250:
            # Zero quantity trade
            payload = {
                "instrument": sym,
                "event_type": "TRADE",
                "price": 180.0,
                "quantity": 0.0,
                "exchange_ts": now + (i * 0.001),
                "sequence": seq,
            }
        elif i == 350:
            # Sequence gap anomaly (skip 10 sequences)
            seq_map[key] += 10
            seq = seq_map[key]
            payload = {
                "instrument": sym,
                "event_type": "TRADE",
                "price": 185.0,
                "quantity": 25.0,
                "exchange_ts": now + (i * 0.001),
                "sequence": seq,
            }
        else:
            # Valid standard quote or trade
            is_quote = (i % 2 == 0)
            if is_quote:
                mid = 180.0 + (i % 10) * 0.1
                payload = {
                    "instrument": sym,
                    "event_type": "QUOTE",
                    "bid": round(mid - 0.02, 2),
                    "ask": round(mid + 0.02, 2),
                    "bid_size": 100,
                    "ask_size": 100,
                    "exchange_ts": now + (i * 0.001),
                    "sequence": seq,
                }
            else:
                price = 180.0 + (i % 10) * 0.1
                payload = {
                    "instrument": sym,
                    "event_type": "TRADE",
                    "price": round(price, 2),
                    "quantity": 50,
                    "exchange_ts": now + (i * 0.001),
                    "sequence": seq,
                }

        raw = RawEvent(
            source=source,
            receive_timestamp=now + (i * 0.001),
            payload=payload,
        )

        # Append to durable WAL
        ingest_log.append(raw)

        # Process through pipeline
        canonical = pipeline.process_one(raw)
        if canonical:
            if canonical.quality_status == QualityStatus.VALID:
                valid_count += 1
            else:
                quarantine_count += 1
            
            # Broadcast to fanout consumers
            fanout.publish_event(
                canonical.instrument_id,
                f"{canonical.event_id}:{canonical.price}:{canonical.quality_status.value}".encode("utf-8"),
                seq=seq,
            )

    pipeline.flush()
    store.commit()
    t1_pipe = time.perf_counter()
    pipeline_duration = t1_pipe - t0_pipe

    # Verify consumer delivery
    delivered_to_consumer = 0
    while True:
        item = fanout.consume_event(client_id, timeout=0.01)
        if not item:
            break
        delivered_to_consumer += 1
    fanout.stop()

    # Query SQLite verification
    canonical_stored = store.read_conn.execute("SELECT COUNT(*) FROM canonical_events").fetchone()[0]
    quarantine_stored = store.read_conn.execute("SELECT COUNT(*) FROM quarantine").fetchone()[0]
    lineage_stored = store.read_conn.execute("SELECT COUNT(*) FROM lineage").fetchone()[0]
    store.close()

    e2e_results = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_events_ingested": total_events,
        "valid_events_count": valid_count,
        "quarantined_events_count": quarantine_count,
        "sqlite_canonical_records": canonical_stored,
        "sqlite_quarantine_records": quarantine_stored,
        "sqlite_lineage_records": lineage_stored,
        "fanout_delivered_to_consumer": delivered_to_consumer,
        "pipeline_duration_seconds": round(pipeline_duration, 4),
        "pipeline_throughput_eps": round(total_events / pipeline_duration, 1),
        "quality_gate_passed": (valid_count > 0 and quarantine_count >= 3 and delivered_to_consumer == total_events),
    }

    out_e2e = os.path.join(_REPO_ROOT, "audit", "phase9", "end_to_end_test_results.json")
    with open(out_e2e, "w", encoding="utf-8") as f:
        json.dump(e2e_results, f, indent=2)
    print(f"[OK] Saved E2E test results to: {out_e2e}")
    print(f"     Ingested: {total_events} | Valid: {valid_count} | Quarantine: {quarantine_count} | Fanout Delivered: {delivered_to_consumer}")

    # 3. Forensic Historical Integrity Verification Campaign
    print("\nExecuting Forensic Historical Integrity Campaign with HistoricalVerifier...")
    verifier = HistoricalVerifier()
    segments = ingest_log._list_segment_files()
    if not segments:
        raise RuntimeError("No IngestLog segment file produced during test!")
    
    active_seg_path = segments[0][1]
    
    # 3A. Valid WAL Segment Audit
    valid_audit = verifier.verify_ingestlog_segment(active_seg_path)
    print(f"[Valid Segment Audit] Records: {valid_audit.records_scanned} | CRC Valid: {valid_audit.crc32_valid_count} | Status: {valid_audit.status}")
    print(f"                      Merkle Root: {valid_audit.merkle_root}")

    # 3B. Corrupted Payload Mutation (Bit-Flip Tamper Detection)
    corrupted_seg_path = os.path.join(test_dir, "segment_corrupted.log")
    with open(active_seg_path, "rb") as f_src, open(corrupted_seg_path, "wb") as f_dst:
        data = bytearray(f_src.read())
        # Flip a bit in the middle of a payload
        corrupt_offset = min(len(data) // 2, len(data) - 10)
        data[corrupt_offset] ^= 0xFF
        f_dst.write(data)

    corrupted_audit = verifier.verify_ingestlog_segment(corrupted_seg_path)
    print(f"[Corrupted Segment Audit] Status: {corrupted_audit.status} | CRC Invalids: {corrupted_audit.crc32_invalid_count}")

    # 3C. Truncated Log Segment (Torn-Tail Detection)
    truncated_seg_path = os.path.join(test_dir, "segment_truncated.log")
    with open(active_seg_path, "rb") as f_src, open(truncated_seg_path, "wb") as f_dst:
        data = f_src.read()
        # Truncate halfway through a frame header
        f_dst.write(data[: len(data) - 45])

    truncated_audit = verifier.verify_ingestlog_segment(truncated_seg_path)
    print(f"[Truncated Segment Audit] Status: {truncated_audit.status} | Details: {truncated_audit.details}")

    # 3D. SQLite Store Verification
    sqlite_audit = verifier.verify_sqlite_store(db_path)
    print(f"[SQLite Store Audit] Status: {sqlite_audit.status} | Records Scanned: {sqlite_audit.records_scanned}")

    historical_results = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "valid_segment_audit": valid_audit.to_dict(),
        "corrupted_segment_audit": corrupted_audit.to_dict(),
        "truncated_segment_audit": truncated_audit.to_dict(),
        "sqlite_store_audit": sqlite_audit.to_dict(),
        "tamper_detection_verified": (corrupted_audit.status == "FAIL" and corrupted_audit.crc32_invalid_count > 0),
        "torn_tail_detection_verified": (len(truncated_audit.details) > 0),
        "merkle_provenance_verified": (valid_audit.merkle_root is not None and len(valid_audit.merkle_root) == 64),
    }

    out_hist = os.path.join(_REPO_ROOT, "audit", "phase9", "historical_integrity_results.json")
    with open(out_hist, "w", encoding="utf-8") as f:
        json.dump(historical_results, f, indent=2)
    print(f"[OK] Saved historical integrity results to: {out_hist}")

    # Clean up test files
    shutil.rmtree(test_dir, ignore_errors=True)
    return e2e_results, historical_results


if __name__ == "__main__":
    run_e2e_campaign()
