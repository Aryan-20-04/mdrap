"""
Forensic benchmark script for MDRAP 6.0.0 post-remediation audit.
Collects empirical data for:
1. SQLite concurrent reader scalability under @_read_synchronized
2. Pure-Python vs Native C Fastpath throughput and latency
3. ProvenanceTracker tick persistence latency and DLQ overflow behavior
4. PEP 427 wheel packaging binary presence audit
"""

import os
import sys
import time
import json
import sqlite3
import tempfile
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.abspath("src"))

from mdrap.models import RawEvent, CanonicalEvent, QualityStatus, EventType
from mdrap.storage import Store
from mdrap.provenance import ProvenanceTracker, create_provenance
from mdrap.quality import QualityEngine
from mdrap.fastpath import HAS_FASTPATH, FastQualityEngine

def benchmark_sqlite_readers():
    print("[1/4] Benchmarking SQLite concurrent readers...")
    import gc, shutil
    results = {}
    tmpdir = tempfile.mkdtemp()
    try:
        db_path = os.path.join(tmpdir, "bench.db")
        store = Store(path=db_path)
        
        # Populate with 2,000 canonical events
        events = [
            CanonicalEvent(
                event_id=f"EVT-{i:06d}",
                instrument_id="AAPL",
                event_type=EventType.QUOTE,
                exchange_timestamp=time.time(),
                receive_timestamp=time.time(),
                sequence_number=i,
                bid_price=150.0 + (i % 100) * 0.01,
                ask_price=150.05 + (i % 100) * 0.01,
                bid_size=100.0,
                ask_size=200.0,
                quality_status=QualityStatus.VALID,
                source="FEED-1"
            )
            for i in range(2000)
        ]
        store.write_canonical_batch(events)
        store.commit()
        
        # Test concurrent reader threads: 1, 2, 4, 8, 16
        for n_threads in [1, 2, 4, 8, 16]:
            queries_per_thread = 50
            total_queries = n_threads * queries_per_thread
            latencies = []
            
            def reader_worker():
                t_lat = []
                for _ in range(queries_per_thread):
                    t0 = time.perf_counter()
                    # Perform query_events
                    res = store.query_events(instrument_id="AAPL", limit=20)
                    t1 = time.perf_counter()
                    t_lat.append((t1 - t0) * 1000.0) # ms
                return t_lat

            t_start = time.perf_counter()
            with ThreadPoolExecutor(max_workers=n_threads) as executor:
                futures = [executor.submit(reader_worker) for _ in range(n_threads)]
                for f in futures:
                    latencies.extend(f.result())
            t_total = time.perf_counter() - t_start

            latencies.sort()
            p50 = latencies[len(latencies) // 2]
            p95 = latencies[int(len(latencies) * 0.95)]
            p99 = latencies[int(len(latencies) * 0.99)]
            qps = total_queries / t_total

            results[f"{n_threads}_threads"] = {
                "threads": n_threads,
                "total_queries": total_queries,
                "total_time_sec": round(t_total, 4),
                "qps": round(qps, 2),
                "latency_p50_ms": round(p50, 3),
                "latency_p95_ms": round(p95, 3),
                "latency_p99_ms": round(p99, 3)
            }
        store.close()
        # Clean up thread-local reader connections across threads before exiting
        def cleanup_worker():
            if hasattr(store, "_local_readers") and hasattr(store._local_readers, "conn"):
                if store._local_readers.conn:
                    try: store._local_readers.conn.close()
                    except Exception: pass
                    store._local_readers.conn = None
        with ThreadPoolExecutor(max_workers=16) as executor:
            list(executor.map(lambda _: cleanup_worker(), range(16)))
    finally:
        gc.collect()
        shutil.rmtree(tmpdir, ignore_errors=True)
    return results

def benchmark_engine_parity():
    print("[2/4] Benchmarking QualityEngine vs Native C Fastpath...")
    results = {
        "has_native_fastpath": HAS_FASTPATH,
        "python_engine": {},
        "fast_engine": {}
    }

    test_events = [
        CanonicalEvent(
            event_id=f"EVT-{i}",
            instrument_id="MSFT",
            event_type=EventType.QUOTE,
            exchange_timestamp=time.time(),
            receive_timestamp=time.time(),
            sequence_number=i,
            bid_price=300.0 + (i % 20) * 0.05,
            ask_price=300.10 + (i % 20) * 0.05,
            bid_size=500.0,
            ask_size=600.0,
            quality_status=QualityStatus.VALID,
            source="FEED-1"
        )
        for i in range(10000)
    ]

    # Benchmark Pure Python QualityEngine
    py_engine = QualityEngine()
    t0 = time.perf_counter()
    for ev in test_events:
        py_engine.evaluate(ev)
    t_py = time.perf_counter() - t0
    results["python_engine"] = {
        "count": len(test_events),
        "total_time_sec": round(t_py, 4),
        "events_per_sec": round(len(test_events) / t_py, 2),
        "mean_latency_us": round((t_py / len(test_events)) * 1_000_000, 2)
    }

    # Benchmark FastQualityEngine
    try:
        fast_engine = FastQualityEngine()
        t0 = time.perf_counter()
        for ev in test_events:
            fast_engine.evaluate(ev)
        t_fast = time.perf_counter() - t0
        results["fast_engine"] = {
            "count": len(test_events),
            "total_time_sec": round(t_fast, 4),
            "events_per_sec": round(len(test_events) / t_fast, 2),
            "mean_latency_us": round((t_fast / len(test_events)) * 1_000_000, 2)
        }
    except Exception as e:
        results["fast_engine"] = {"error": str(e)}

    return results

def audit_provenance_and_dlq():
    print("[3/4] Auditing ProvenanceTracker latency and DLQ overflow...")
    results = {}
    tmpdir = tempfile.mkdtemp()
    try:
        db_path = os.path.join(tmpdir, "prov.db")
        tracker = ProvenanceTracker(db_path=db_path)

        ev = CanonicalEvent(
            event_id="EVT-TEST",
            instrument_id="NVDA",
            event_type=EventType.QUOTE,
            exchange_timestamp=time.time(),
            receive_timestamp=time.time(),
            sequence_number=1,
            bid_price=120.0,
            ask_price=120.05,
            bid_size=100.0,
            ask_size=200.0,
            quality_status=QualityStatus.VALID,
            source="FEED-1"
        )
        raw = RawEvent(source="FEED-1", payload={"data": 123})

        # Measure 100 records
        t0 = time.perf_counter()
        for i in range(100):
            prov = create_provenance(raw, ev, f"trans_{i}")
            tracker.record(prov)
        t_rec = time.perf_counter() - t0
        
        # Test DLQ overflow: fill 10,050 items into _pending_retry (maxlen=10000)
        err = sqlite3.OperationalError("database is locked")
        for _ in range(10050):
            tracker._handle_persistence_failure(prov, err)
        
        results["record_100_latency_sec"] = round(t_rec, 4)
        results["record_latency_mean_ms"] = round((t_rec / 100) * 1000.0, 3)
        results["dlq_len"] = len(tracker._pending_retry)
        results["dlq_maxlen"] = tracker._pending_retry.maxlen
        results["dropped_counter_after_overflow"] = tracker.dropped_provenance_count
        results["dropped_counter_missed_discards"] = 10050 - tracker.dropped_provenance_count - tracker._pending_retry.maxlen
        tracker.close()
    finally:
        import gc, shutil
        gc.collect()
        shutil.rmtree(tmpdir, ignore_errors=True)
        
    return results

def audit_wheel_packaging():
    print("[4/4] Auditing PEP 427 wheel packaging contents...")
    results = {
        "wheels_found": []
    }
    dist_dir = "dist"
    if os.path.exists(dist_dir):
        for fname in os.listdir(dist_dir):
            if fname.endswith(".whl"):
                fpath = os.path.join(dist_dir, fname)
                binaries = []
                with zipfile.ZipFile(fpath, "r") as z:
                    for name in z.namelist():
                        if name.endswith((".dll", ".so", ".pyd", ".dylib")):
                            binaries.append(name)
                results["wheels_found"].append({
                    "filename": fname,
                    "binaries_present": binaries,
                    "is_pure_python_tag": "py3-none-any" in fname,
                    "violates_pep427": ("py3-none-any" in fname and len(binaries) > 0)
                })
    return results

if __name__ == "__main__":
    output = {}
    try:
        output["sqlite_readers"] = benchmark_sqlite_readers()
    except Exception as e:
        output["sqlite_readers"] = {"error": str(e)}

    try:
        output["engine_parity"] = benchmark_engine_parity()
    except Exception as e:
        output["engine_parity"] = {"error": str(e)}

    try:
        output["provenance_dlq"] = audit_provenance_and_dlq()
    except Exception as e:
        output["provenance_dlq"] = {"error": str(e)}

    try:
        output["packaging"] = audit_wheel_packaging()
    except Exception as e:
        output["packaging"] = {"error": str(e)}

    with open("audit/v6.0.0/raw/forensic_benchmarks.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print("Forensic benchmark data written to audit/v6.0.0/raw/forensic_benchmarks.json")
