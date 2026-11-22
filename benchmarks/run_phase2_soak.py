"""
MDRAP Full-System Benchmark Suite - Phase 2: Long-Duration Reliability Soak Harness
Supports 30-minute and multi-hour continuous end-to-end reliability verification:
Producer -> MarketDataDaemon -> TCP StreamClient -> ObserverClient -> ObserverState -> ObserverExporter -> JSONL

Collects:
- Fixed 1-minute window performance summaries (throughput, latencies, CPU, RSS, queue depth).
- 10-minute memory trend summaries.
- Independent event-ID reconciliation.
- Multi-domain sequence tracking (transport stream vs upstream feed).
- Latency drift from initial window to final window.
- Graceful recovery under controlled network interruption without counter resets.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import sys
import tempfile
import time
from typing import Any, Dict, List, Set

# Ensure paths
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "external_sidecar", "mdrap_observer")))

from mdrap.models import RawEvent
from mdrap.service import MarketDataDaemon
from observer.client import ObserverClient
from observer.exporter import ObserverExporter
from observer.state import ObserverState, VENUES


def get_process_rss_mb() -> float:
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / (1024.0 * 1024.0)
    except Exception:
        return 0.0


def run_soak_drill(
    duration_s: float = 1800.0,
    target_eps: float = 1000.0,
    interruption_at_s: float | None = None,
    out_json_path: str = "audit/benchmark/phase2/raw/soak_30min.json",
) -> Dict[str, Any]:
    print("=" * 80)
    print(f" MDRAP Phase 2 Soak Harness: duration={duration_s:.1f}s ({duration_s/60:.1f}m), target={target_eps:.0f} EPS")
    print("=" * 80)

    os.environ["MDRAP_API_KEY_SALT"] = "soak_bench_salt_phase2_98765"
    os.environ["MDRAP_DEMO"] = "1"

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    export_dir = tempfile.mkdtemp(prefix="mdrap_phase2_soak_")

    daemon = MarketDataDaemon(
        host="127.0.0.1",
        port=0,
        db_path=db_path,
        use_live=False,
        enable_shm=False,
        sim_speed_eps=0.0,
    )
    daemon.start(blocking=False)
    time.sleep(0.3)
    port = daemon.port

    state = ObserverState()
    exporter = ObserverExporter(output_dir=export_dir, flush_interval_s=0.25, queue_size=100000)
    exporter.start()

    # Event tracking and reconciliation data structures
    all_latencies_us: List[float] = []
    received_event_ids: Set[int] = set()
    received_count = 0
    max_queue_depth_observed = 0
    initial_rss = get_process_rss_mb()
    peak_rss = initial_rss

    # Window metrics accumulators
    window_latencies: List[float] = []
    window_sent = 0
    window_received = 0
    minute_summaries: List[Dict[str, Any]] = []

    def on_event(evt: dict):
        nonlocal received_count, window_received, max_queue_depth_observed
        t_recv = time.time()
        state.process_event(evt)
        exporter.record_event(evt)
        received_count += 1
        window_received += 1

        seq_id = evt.get("seq")
        if seq_id is not None:
            received_event_ids.add(seq_id)

        q_sz = exporter._queue.qsize()
        if q_sz > max_queue_depth_observed:
            max_queue_depth_observed = q_sz

        t_pub = evt.get("ts") or evt.get("broadcast_ts") or 0.0
        if t_pub > 0:
            lat = (t_recv - t_pub) * 1e6
            if lat >= 0:
                all_latencies_us.append(lat)
                window_latencies.append(lat)

    client = ObserverClient(mode="tcp", host="127.0.0.1", port=port, on_event_callback=on_event)
    client.start()
    time.sleep(0.3)

    symbols = ["BTCUSDT", "BTC-USD", "ETHUSDT", "ETH-USD"]
    venues = VENUES

    producer_sent = 0
    producer_sent_ids: Set[int] = set()
    t_start = time.perf_counter()
    last_window_t = t_start
    current_minute = 1
    interruption_occurred = False

    batch_size = 10
    batch_interval = batch_size / target_eps
    next_batch_time = time.perf_counter()

    try:
        import psutil
        proc = psutil.Process(os.getpid())
        proc.cpu_percent(interval=None)  # prime cpu percent
    except Exception:
        proc = None

    print(f"[SOAK] Injection running. Sampling in 1-minute fixed windows...")

    try:
        while True:
            t_now = time.perf_counter()
            elapsed = t_now - t_start
            if elapsed >= duration_s:
                break

            # Controlled interruption injection if configured
            if interruption_at_s and elapsed >= interruption_at_s and not interruption_occurred:
                print(f"\n  [INJECT] Inducing controlled TCP link reset at T+{elapsed:.1f}s...")
                if client._current_client and client._current_client.sock:
                    try:
                        client._current_client.sock.close()
                    except Exception:
                        pass
                interruption_occurred = True
                print("  [INJECT] Client socket closed. Verifying automatic reconnect...")

            # Inject batch
            for _ in range(batch_size):
                producer_sent += 1
                window_sent += 1
                producer_sent_ids.add(producer_sent)

                venue = venues[producer_sent % len(venues)]
                symbol = symbols[producer_sent % len(symbols)]
                base_p = 65000.0 if "BTC" in symbol else 3400.0
                price = base_p + (producer_sent % 100) * 0.1
                t_pub = time.perf_counter()

                raw = RawEvent(
                    source=venue,
                    receive_timestamp=t_pub,
                    payload={
                        "instrument": symbol,
                        "event_type": "TICK",
                        "price": price,
                        "bid": price - 0.5,
                        "ask": price + 0.5,
                        "bid_size": 10.0,
                        "ask_size": 10.0,
                        "quantity": 1.0,
                        "sequence": producer_sent,
                        "exchange_ts": time.time(),
                    },
                )
                daemon._process_and_broadcast(raw)

            # End-of-minute window evaluation (every 60.0s)
            if t_now - last_window_t >= 60.0:
                w_duration = t_now - last_window_t
                window_latencies.sort()
                wn = len(window_latencies) or 1

                cpu_pct = proc.cpu_percent(interval=None) if proc else 0.0
                rss_mb = get_process_rss_mb()

                w_summary = {
                    "minute": current_minute,
                    "elapsed_s": round(elapsed, 1),
                    "events_sent": window_sent,
                    "events_received": window_received,
                    "throughput_eps": round(window_received / max(0.001, w_duration), 1),
                    "cpu_percent": round(cpu_pct, 1),
                    "rss_mb": round(rss_mb, 2),
                    "queue_depth_instant": exporter._queue.qsize(),
                    "queue_depth_max": max_queue_depth_observed,
                    "dropped_records": exporter.dropped_records,
                    "write_errors": exporter.write_errors,
                    "reconnect_count": client.reconnect_count,
                    "latency_us": {
                        "p50": round(window_latencies[int(wn * 0.50)], 2) if wn > 1 else 0.0,
                        "p90": round(window_latencies[int(wn * 0.90)], 2) if wn > 1 else 0.0,
                        "p95": round(window_latencies[int(wn * 0.95)], 2) if wn > 1 else 0.0,
                        "p99": round(window_latencies[int(wn * 0.99)], 2) if wn > 1 else 0.0,
                        "p99_9": round(window_latencies[int(wn * 0.999)], 2) if wn > 1 else 0.0,
                        "max": round(window_latencies[-1], 2) if window_latencies else 0.0,
                    },
                    "stream_gaps": state.stream_health.gaps,
                    "feed_gaps": sum(v.feed_gaps for v in state.health.values()),
                }
                minute_summaries.append(w_summary)
                print(
                    f"  [Min {current_minute:02d} | T+{int(elapsed)}s] "
                    f"Sent: {window_sent:,} | Recv: {window_received:,} | "
                    f"EPS: {w_summary['throughput_eps']:>6,.1f} | "
                    f"p50: {w_summary['latency_us']['p50']:>6.1f}us | "
                    f"p99: {w_summary['latency_us']['p99']:>7.1f}us | "
                    f"RSS: {rss_mb:>5.1f}MB | Q_max: {max_queue_depth_observed} | "
                    f"Recon: {client.reconnect_count}",
                    flush=True,
                )

                # Reset window accumulators
                window_latencies = []
                window_sent = 0
                window_received = 0
                last_window_t = t_now
                current_minute += 1

            # Pacing
            next_batch_time += batch_interval
            sleep_s = next_batch_time - time.perf_counter()
            if sleep_s > 0.001:
                time.sleep(sleep_s)
            elif sleep_s < -0.1:
                next_batch_time = time.perf_counter()

    finally:
        print("\n[SOAK] Injection phase complete. Allowing queues and socket to drain...", flush=True)
        time.sleep(1.5)
        client.stop()
        daemon.stop()
        exporter.stop()

    t_total = time.perf_counter() - t_start
    final_rss = get_process_rss_mb()
    peak_rss = max(peak_rss, final_rss)

    # Emit final partial minute summary if events remaining
    if window_sent > 0 or window_received > 0:
        w_duration = max(0.001, t_total - (current_minute - 1) * 60.0)
        window_latencies.sort()
        wn = len(window_latencies) or 1
        minute_summaries.append({
            "minute": current_minute,
            "elapsed_s": round(t_total, 1),
            "events_sent": window_sent,
            "events_received": window_received,
            "throughput_eps": round(window_received / w_duration, 1),
            "cpu_percent": round(proc.cpu_percent(interval=None) if proc else 0.0, 1),
            "rss_mb": round(final_rss, 2),
            "queue_depth_instant": exporter._queue.qsize(),
            "queue_depth_max": max_queue_depth_observed,
            "dropped_records": exporter.dropped_records,
            "write_errors": exporter.write_errors,
            "reconnect_count": client.reconnect_count,
            "latency_us": {
                "p50": round(window_latencies[int(wn * 0.50)], 2) if wn > 1 else 0.0,
                "p90": round(window_latencies[int(wn * 0.90)], 2) if wn > 1 else 0.0,
                "p95": round(window_latencies[int(wn * 0.95)], 2) if wn > 1 else 0.0,
                "p99": round(window_latencies[int(wn * 0.99)], 2) if wn > 1 else 0.0,
                "p99_9": round(window_latencies[int(wn * 0.999)], 2) if wn > 1 else 0.0,
                "max": round(window_latencies[-1], 2) if window_latencies else 0.0,
            },
            "stream_gaps": state.stream_health.gaps,
            "feed_gaps": sum(v.feed_gaps for v in state.health.values()),
        })

    # Reconcile event IDs
    missing_ids = producer_sent_ids - received_event_ids
    unexpected_ids = received_event_ids - producer_sent_ids
    reconciliation_matched = (len(missing_ids) == 0 and len(unexpected_ids) == 0 and producer_sent == received_count)

    # 10-minute memory trend summaries
    memory_10min_trends = []
    chunk_size = 10
    for chunk_idx in range(0, len(minute_summaries), chunk_size):
        chunk = minute_summaries[chunk_idx : chunk_idx + chunk_size]
        if chunk:
            m_start = chunk[0]["rss_mb"]
            m_end = chunk[-1]["rss_mb"]
            growth = m_end - m_start
            rate = growth / max(1, len(chunk))
            memory_10min_trends.append({
                "window": f"Min {chunk[0]['minute']} - Min {chunk[-1]['minute']}",
                "start_rss_mb": m_start,
                "end_rss_mb": m_end,
                "net_change_mb": round(growth, 2),
                "growth_rate_mb_per_min": round(rate, 3),
            })

    # Latency drift: compare first 10 minutes vs final 10 minutes
    initial_10m_lats = []
    final_10m_lats = []
    n_mins = len(minute_summaries)
    if n_mins >= 2:
        init_mins = minute_summaries[: min(10, n_mins // 2)]
        final_mins = minute_summaries[-min(10, n_mins // 2) :]
        init_p50 = sum(m["latency_us"]["p50"] for m in init_mins) / len(init_mins)
        init_p99 = sum(m["latency_us"]["p99"] for m in init_mins) / len(init_mins)
        fin_p50 = sum(m["latency_us"]["p50"] for m in final_mins) / len(final_mins)
        fin_p99 = sum(m["latency_us"]["p99"] for m in final_mins) / len(final_mins)
        latency_drift = {
            "initial_window_avg_p50_us": round(init_p50, 2),
            "initial_window_avg_p99_us": round(init_p99, 2),
            "final_window_avg_p50_us": round(fin_p50, 2),
            "final_window_avg_p99_us": round(fin_p99, 2),
            "drift_p50_us": round(fin_p50 - init_p50, 2),
            "drift_p99_us": round(fin_p99 - init_p99, 2),
        }
    else:
        latency_drift = {}

    all_latencies_us.sort()
    tot_n = len(all_latencies_us) or 1

    # Verify JSONL integrity & SHA-256
    events_file = exporter.events_file
    jsonl_valid = True
    jsonl_lines = 0
    jsonl_sha256 = ""
    file_sz = 0
    if os.path.exists(events_file):
        file_sz = os.path.getsize(events_file)
        hasher = hashlib.sha256()
        with open(events_file, "rb") as fp:
            for line in fp:
                hasher.update(line)
                line_str = line.decode("utf-8", errors="replace").strip()
                if line_str:
                    try:
                        json.loads(line_str)
                        jsonl_lines += 1
                    except Exception:
                        jsonl_valid = False
        jsonl_sha256 = hasher.hexdigest()

    shutil.rmtree(export_dir, ignore_errors=True)
    try:
        os.remove(db_path)
    except Exception:
        pass

    results = {
        "benchmark_type": "SOAK_TEST",
        "duration_target_s": duration_s,
        "duration_actual_s": round(t_total, 3),
        "target_eps": target_eps,
        "effective_throughput_eps": round(received_count / max(0.001, t_total), 1),
        "total_producer_events": producer_sent,
        "total_observer_events": received_count,
        "events_written_to_jsonl": exporter.events_written,
        "delivery_rate_pct": round((received_count / max(1, producer_sent)) * 100.0, 4),
        "reconciliation": {
            "matched": reconciliation_matched,
            "missing_event_ids_count": len(missing_ids),
            "unexpected_event_ids_count": len(unexpected_ids),
            "sample_missing_ids": sorted(list(missing_ids))[:10],
        },
        "sequence_domains": {
            "stream_gaps": state.stream_health.gaps,
            "stream_resets": state.stream_health.resets,
            "stream_duplicates": state.stream_health.duplicates,
            "feed_gaps": sum(v.feed_gaps for v in state.health.values()),
            "feed_duplicates": sum(v.feed_duplicates for v in state.health.values()),
            "feed_out_of_order": sum(v.feed_out_of_order for v in state.health.values()),
        },
        "queues_and_backpressure": {
            "max_queue_depth_observed": max_queue_depth_observed,
            "dropped_records": exporter.dropped_records,
            "write_errors": exporter.write_errors,
        },
        "transport_reconnects": {
            "reconnect_count": client.reconnect_count,
            "interruption_injected": interruption_occurred,
            "reconnected_cleanly": (client.reconnect_count > 0 if interruption_occurred else True),
        },
        "latency_overall_us": {
            "p50": round(all_latencies_us[int(tot_n * 0.50)], 2) if tot_n > 1 else 0.0,
            "p90": round(all_latencies_us[int(tot_n * 0.90)], 2) if tot_n > 1 else 0.0,
            "p95": round(all_latencies_us[int(tot_n * 0.95)], 2) if tot_n > 1 else 0.0,
            "p99": round(all_latencies_us[int(tot_n * 0.99)], 2) if tot_n > 1 else 0.0,
            "p99_9": round(all_latencies_us[int(tot_n * 0.999)], 2) if tot_n > 1 else 0.0,
            "max": round(all_latencies_us[-1], 2) if all_latencies_us else 0.0,
        },
        "latency_drift": latency_drift,
        "memory_progression_mb": {
            "initial": round(initial_rss, 2),
            "peak": round(peak_rss, 2),
            "final": round(final_rss, 2),
            "net_growth": round(final_rss - initial_rss, 2),
            "trends_10min": memory_10min_trends,
        },
        "jsonl_audit": {
            "is_valid_json": jsonl_valid,
            "lines_verified": jsonl_lines,
            "file_size_bytes": file_sz,
            "sha256": jsonl_sha256,
        },
        "minute_windows": minute_summaries,
    }

    os.makedirs(os.path.dirname(out_json_path), exist_ok=True)
    with open(out_json_path, "w", encoding="utf-8") as fp:
        json.dump(results, fp, indent=2)

    print("\n" + "=" * 80)
    print(" Soak Run Completed Successfully")
    print("=" * 80)
    print(f" Actual Duration:     {results['duration_actual_s']:.1f} s")
    print(f" Events Sent:        {results['total_producer_events']:,}")
    print(f" Events Received:    {results['total_observer_events']:,}")
    print(f" Delivery Rate:      {results['delivery_rate_pct']:.4f}%")
    print(f" Reconciliation:     {'MATCHED (100%)' if reconciliation_matched else 'FAILED'}")
    print(f" Latency p50:        {results['latency_overall_us']['p50']:.1f} us")
    print(f" Latency p99:        {results['latency_overall_us']['p99']:.1f} us")
    print(f" Peak RSS:           {results['memory_progression_mb']['peak']:.1f} MB (Net: {results['memory_progression_mb']['net_growth']:+.1f} MB)")
    print(f" Max Queue Depth:    {results['queues_and_backpressure']['max_queue_depth_observed']}")
    print(f" Stream Gaps:        {results['sequence_domains']['stream_gaps']}")
    print(f" Feed Gaps:          {results['sequence_domains']['feed_gaps']}")
    print(f" JSONL Valid:        {results['jsonl_audit']['is_valid_json']} ({results['jsonl_audit']['lines_verified']:,} lines)")
    print(f" Saved To:           {out_json_path}")
    print("=" * 80)

    return results


def main():
    parser = argparse.ArgumentParser(description="MDRAP Phase 2 Soak Testing Harness")
    parser.add_argument("--duration", type=float, default=1800.0, help="Duration in seconds (default: 1800 = 30m)")
    parser.add_argument("--rate", type=float, default=1000.0, help="Target ingestion rate EPS (default: 1000)")
    parser.add_argument("--interruption-at", type=float, default=None, help="Inject controlled interruption at T+seconds")
    parser.add_argument("--out", type=str, default="audit/benchmark/phase2/raw/soak_30min.json", help="Output path")
    args = parser.parse_args()

    run_soak_drill(
        duration_s=args.duration,
        target_eps=args.rate,
        interruption_at_s=args.interruption_at,
        out_json_path=args.out,
    )


if __name__ == "__main__":
    main()
