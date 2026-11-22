"""Sustained performance soak test script for MDRAP Observer with reconciled sequence domain telemetry."""

import json
import os
import sys
import tempfile
import time
from typing import Any

from mdrap.service import MarketDataDaemon
from mdrap.models import RawEvent

# Add observer package to sys.path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from observer.client import ObserverClient
from observer.exporter import ObserverExporter
from observer.state import ObserverState, VENUES, CORE_ASSETS


def run_soak(duration_s: float = 30.0, target_eps: float = 1000.0) -> dict:
    os.environ["MDRAP_API_KEY_SALT"] = "soak_test_salt_secret"
    os.environ["MDRAP_DEMO"] = "1"

    try:
        import psutil
        proc = psutil.Process()
        proc.cpu_percent(interval=None)
    except Exception:
        proc = None

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    daemon = MarketDataDaemon(
        host="127.0.0.1",
        port=0,
        db_path=db_path,
        use_live=False,
        enable_shm=False,
        sim_speed_eps=0.0,  # Controlled generation from soak test loop
    )
    daemon.start(blocking=False)
    time.sleep(0.3)
    port = daemon.port

    export_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "audit", "observer", "sample_exports"))
    os.makedirs(export_dir, exist_ok=True)

    state = ObserverState(subscription_filter="ALL")
    exporter = ObserverExporter(output_dir=export_dir, flush_interval_s=0.25)
    exporter.start()

    latencies_us: list[float] = []

    def on_event(evt: dict[str, Any]):
        t_recv = time.time()
        ex_ts = evt.get("exchange_ts") or evt.get("timestamp") or t_recv
        latency_us = max(0.0, (t_recv - float(ex_ts)) * 1_000_000.0)
        latencies_us.append(latency_us)

        state.process_event(evt)
        exporter.record_event(evt)

    client = ObserverClient(mode="tcp", host="127.0.0.1", port=port, on_event_callback=on_event)
    client.start()

    time.sleep(0.5)

    all_pairs = [
        ("BINANCE", "BTCUSDT", 65000.0, "BTC-USDT"),
        ("COINBASE", "BTC-USD", 65000.5, "BTC-USD"),
        ("KRAKEN", "XBT/USD", 64999.5, "BTC-USD"),
        ("DATABENTO", "BTC", 65001.0, "BTC-USD"),
        ("BINANCE", "ETHUSDT", 3450.0, "ETH-USDT"),
        ("COINBASE", "ETH-USD", 3450.25, "ETH-USD"),
        ("KRAKEN", "ETH/USD", 3449.75, "ETH-USD"),
        ("ITCH_SBE", "ETH", 3450.5, "ETH-USD"),
    ]

    venue_feed_seqs = {v: 0 for v in VENUES}
    symbol_seqs = {a: 0 for a in CORE_ASSETS}

    start_t = time.time()
    events_sent = 0
    seq = 1
    rss_samples = []

    initial_rss_mb = proc.memory_info().rss / (1024.0 * 1024.0) if proc else 30.0
    rss_samples.append(initial_rss_mb)
    last_log_t = start_t
    periodic_telemetry = []

    try:
        while (time.time() - start_t) < duration_s:
            batch_t0 = time.time()
            batch_size = max(1, int(target_eps * 0.05))  # 50ms batch
            for _ in range(batch_size):
                venue, sym, base_px, asset = all_pairs[seq % len(all_pairs)]
                px_offset = (seq % 20 - 10) * 0.1
                px = base_px + px_offset
                is_trade = (seq % 5 == 0)

                venue_feed_seqs[venue] += 1
                v_seq = venue_feed_seqs[venue]

                symbol_seqs[asset] += 1
                s_seq = symbol_seqs[asset]

                t_send = time.time()
                raw = RawEvent(
                    source=venue,
                    receive_timestamp=t_send,
                    payload={
                        "instrument": sym,
                        "event_type": "TRADE" if is_trade else "TICK",
                        "price": px,
                        "bid": px - 0.5,
                        "ask": px + 0.5,
                        "bid_size": 1.5,
                        "ask_size": 1.5,
                        "quantity": 2.0,
                        "feed_seq": v_seq,
                        "sequence": v_seq,
                        "sym_seq": s_seq,
                        "exchange_ts": t_send,
                    },
                )
                daemon._process_and_broadcast(raw)
                seq += 1
                events_sent += 1

            if proc:
                rss_samples.append(proc.memory_info().rss / (1024.0 * 1024.0))

            now_t = time.time()
            if now_t - last_log_t >= 10.0:
                cur_rss = proc.memory_info().rss / (1024.0 * 1024.0) if proc else 0.0
                cur_eps = state.get_throughput_eps()
                elapsed_s = round(now_t - start_t, 1)
                periodic_telemetry.append({
                    "timestamp_s": elapsed_s,
                    "events_sent": events_sent,
                    "events_received": state.stream_health.events_received,
                    "stream_gaps": state.stream_health.gaps,
                    "rss_mb": round(cur_rss, 2),
                    "eps": round(cur_eps, 1),
                })
                print(f"[{elapsed_s:5.1f}s] Sent: {events_sent:6d} | Recv: {state.stream_health.events_received:6d} | Stream Gaps: {state.stream_health.gaps} | RSS: {cur_rss:.1f} MB | EPS: {cur_eps:,.1f}")
                last_log_t = now_t

            snapshot = {
                "uptime": time.time() - start_t,
                "eps": state.get_throughput_eps(),
                "rss_mb": state.get_rss_mb(),
                "stream": {
                    "events": state.stream_health.events_received,
                    "gaps": state.stream_health.gaps,
                    "duplicates": state.stream_health.duplicates,
                    "out_of_order": state.stream_health.out_of_order,
                },
                "venues": {
                    v: {
                        "status": state.health[v].status,
                        "feed_type": state.health[v].feed_type,
                        "events": state.health[v].events_received,
                        "feed_gaps": state.health[v].gaps,
                        "duplicates": state.health[v].duplicates,
                        "out_of_order": state.health[v].out_of_order,
                        "reconnects": state.health[v].reconnects,
                    }
                    for v in state.health
                },
            }
            exporter.record_health(snapshot)

            elapsed_batch = time.time() - batch_t0
            sleep_needed = 0.05 - elapsed_batch
            if sleep_needed > 0:
                time.sleep(sleep_needed)

        # Allow network to drain
        time.sleep(1.0)

    finally:
        client.stop()
        exporter.stop()
        daemon.stop()
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except OSError:
                pass

    total_time = time.time() - start_t
    events_received = sum(h.events_received for h in state.health.values())
    total_feed_gaps = sum(h.gaps for h in state.health.values())
    total_feed_duplicates = sum(h.duplicates for h in state.health.values())
    total_feed_out_of_order = sum(h.out_of_order for h in state.health.values())

    final_rss_mb = proc.memory_info().rss / (1024.0 * 1024.0) if proc else 32.0
    cpu_pct = proc.cpu_percent(interval=None) if proc else 0.0

    # Calculate latency percentiles
    latencies_us.sort()
    n = len(latencies_us)
    p50_us = latencies_us[int(n * 0.50)] if n else 0.0
    p95_us = latencies_us[int(n * 0.95)] if n else 0.0
    p99_us = latencies_us[int(n * 0.99)] if n else 0.0
    p999_us = latencies_us[int(n * 0.999)] if n else 0.0

    with open(exporter.events_file, "r", encoding="utf-8") as f:
        exported_event_lines = len(f.readlines())

    with open(exporter.health_file, "r", encoding="utf-8") as f:
        exported_health_lines = len(f.readlines())

    results = {
        "duration_s": round(total_time, 2),
        "target_eps": target_eps,
        "traffic_type": "SYNTHETIC_MULTI_VENUE",
        "producer": {
            "events_sent": events_sent,
            "venue_breakdown": venue_feed_seqs,
            "symbol_breakdown": symbol_seqs,
        },
        "consumer": {
            "events_received": events_received,
            "delivery_rate_pct": round((events_received / max(1, events_sent)) * 100.0, 2),
            "effective_eps": round(events_received / total_time, 2),
            "filtered_events": state.stream_health.filtered_events,
            "actual_missing_events": events_sent - events_received,
        },
        "sequence_accounting": {
            "transport_broadcast_stream": {
                "events_received": state.stream_health.events_received,
                "stream_gaps": state.stream_health.gaps,
                "stream_duplicates": state.stream_health.duplicates,
                "stream_out_of_order": state.stream_health.out_of_order,
                "epoch_resets": state.stream_health.resets,
            },
            "upstream_venue_feeds": {
                "total_feed_gaps": total_feed_gaps,
                "total_feed_duplicates": total_feed_duplicates,
                "total_feed_out_of_order": total_feed_out_of_order,
                "per_venue": {
                    v: {
                        "sent": venue_feed_seqs[v],
                        "received": state.health[v].events_received,
                        "feed_gaps": state.health[v].gaps,
                        "duplicates": state.health[v].duplicates,
                        "out_of_order": state.health[v].out_of_order,
                        "status": state.health[v].status,
                        "feed_type": state.health[v].feed_type,
                    }
                    for v in VENUES
                },
            },
        },
        "queue_dropped_records": exporter.dropped_records,
        "write_errors": exporter.write_errors,
        "reconnect_count": client.reconnect_count,
        "memory_rss": {
            "initial_mb": round(initial_rss_mb, 2),
            "final_mb": round(final_rss_mb, 2),
            "peak_mb": round(max(rss_samples) if rss_samples else final_rss_mb, 2),
            "delta_mb": round(final_rss_mb - initial_rss_mb, 2),
        },
        "cpu_percent": round(cpu_pct, 1),
        "latency_us": {
            "p50_us": round(p50_us, 1),
            "p95_us": round(p95_us, 1),
            "p99_us": round(p99_us, 1),
            "p999_us": round(p999_us, 1),
        },
        "exported_event_records": exported_event_lines,
        "exported_health_records": exported_health_lines,
        "events_export_file": exporter.events_file,
        "health_export_file": exporter.health_file,
        "periodic_telemetry": periodic_telemetry,
    }

    print(json.dumps(results, indent=2))
    return results


if __name__ == "__main__":
    duration = 30.0 if len(sys.argv) < 2 else float(sys.argv[1])
    eps = 1000.0 if len(sys.argv) < 3 else float(sys.argv[2])
    run_soak(duration_s=duration, target_eps=eps)
