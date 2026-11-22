"""
MDRAP Full-System Benchmark Suite - Phase 3: Transport Benchmark (TCP vs SHM)
Measures throughput, end-to-end transport latency (p50, p90, p95, p99, p99.9, max),
resource utilization (CPU, RSS), and multi-consumer scaling (1, 2, 4 consumers).
"""

from __future__ import annotations

import json
import os
import socket
import struct
import sys
import threading
import time
from typing import Any, Dict, List

# Ensure src in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from mdrap.shm import SHMWriter, SHMReader


def get_rss_mb() -> float:
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except Exception:
        return 0.0


def benchmark_shm_scaling(num_events: int = 50_000, num_consumers: int = 1) -> Dict[str, Any]:
    ring_name = f"mdrap_shm_bench_{num_consumers}_{int(time.time()*1000)%1000000}"
    slot_count = 65536  # Power of 2 ring
    writer = SHMWriter(ring_name, slot_count=slot_count)
    
    rss_start = get_rss_mb()
    t_cpu_start = time.process_time()

    consumer_latencies: List[List[float]] = [[] for _ in range(num_consumers)]
    consumer_counts = [0] * num_consumers
    stop_event = threading.Event()
    start_event = threading.Event()

    def consumer_worker(cid: int):
        reader = SHMReader(ring_name)
        latencies = []
        count = 0
        curr_seq = 1
        start_event.wait()
        while not stop_event.is_set() and count < num_events:
            slot = reader.read_slot(curr_seq)
            if slot is not None:
                t_now = time.perf_counter()
                t_pub = slot.get("broadcast_ts", 0.0)
                if t_pub > 0:
                    lat_us = (t_now - t_pub) * 1e6
                    latencies.append(lat_us)
                count += 1
                curr_seq += 1
            else:
                head = struct.unpack_from("<Q", reader.shm.buf, 24)[0]
                if head > curr_seq + reader.slot_count:
                    # Overrun: jump to head - slot_count + 1
                    curr_seq = head - reader.slot_count + 1
                else:
                    time.sleep(0.000005)  # 5us yield
        reader.close()
        consumer_latencies[cid] = latencies
        consumer_counts[cid] = count

    threads = [threading.Thread(target=consumer_worker, args=(i,), daemon=True) for i in range(num_consumers)]
    for t in threads:
        t.start()

    time.sleep(0.05)  # Let threads attach to SHM
    start_event.set()
    t0 = time.perf_counter()

    for i in range(1, num_events + 1):
        t_pub = time.perf_counter()
        writer.write_tick(
            seq=i,
            symbol="BTCUSDT",
            source="BINANCE",
            price=65000.0 + (i % 100) * 0.1,
            size=1.5,
            bid=64999.0,
            ask=65001.0,
            status="VALID",
            exchange_ts=1700000000.0,
            ingest_ts=t_pub,
            broadcast_ts=t_pub,
            engine_us=2.5,
        )

    # Wait for consumers to receive
    timeout = 10.0
    t_wait_start = time.perf_counter()
    while any(c < num_events for c in consumer_counts) and (time.perf_counter() - t_wait_start < timeout):
        time.sleep(0.005)

    elapsed = time.perf_counter() - t0
    stop_event.set()
    for t in threads:
        t.join(timeout=1.0)

    t_cpu = time.process_time() - t_cpu_start
    rss_end = get_rss_mb()
    writer.close()

    # Aggregate consumer latencies
    all_lats = [lat for l_list in consumer_latencies for lat in l_list if lat >= 0]
    all_lats.sort()
    n = len(all_lats) or 1

    return {
        "transport": "SHM",
        "num_consumers": num_consumers,
        "target_events": num_events,
        "received_events_per_consumer": consumer_counts,
        "total_deliveries": sum(consumer_counts),
        "delivery_rate_pct": (sum(consumer_counts) / (num_events * num_consumers)) * 100.0,
        "elapsed_s": round(elapsed, 4),
        "throughput_eps": round((num_events * num_consumers) / elapsed, 1),
        "producer_eps": round(num_events / elapsed, 1),
        "latency_us": {
            "p50": round(all_lats[int(n * 0.50)], 2) if n > 1 else 0.0,
            "p90": round(all_lats[int(n * 0.90)], 2) if n > 1 else 0.0,
            "p95": round(all_lats[int(n * 0.95)], 2) if n > 1 else 0.0,
            "p99": round(all_lats[int(n * 0.99)], 2) if n > 1 else 0.0,
            "p99_9": round(all_lats[int(n * 0.999)], 2) if n > 1 else 0.0,
            "max": round(all_lats[-1], 2) if all_lats else 0.0,
        },
        "cpu_time_s": round(t_cpu, 3),
        "rss_mb": {
            "start": round(rss_start, 2),
            "end": round(rss_end, 2),
            "growth": round(rss_end - rss_start, 2),
        },
    }


def benchmark_tcp_scaling(num_events: int = 50_000, num_consumers: int = 1) -> Dict[str, Any]:
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind(("127.0.0.1", 0))
    port = server_sock.getsockname()[1]
    server_sock.listen(num_consumers + 2)

    client_conns: List[socket.socket] = []
    accept_event = threading.Event()

    def server_acceptor():
        for _ in range(num_consumers):
            conn, _ = server_sock.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            client_conns.append(conn)
        accept_event.set()

    accept_thread = threading.Thread(target=server_acceptor, daemon=True)
    accept_thread.start()

    consumer_latencies: List[List[float]] = [[] for _ in range(num_consumers)]
    consumer_counts = [0] * num_consumers
    client_threads = []

    def client_worker(cid: int):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        s.connect(("127.0.0.1", port))
        
        raw_buf = bytearray()
        latencies = []
        count = 0
        while count < num_events:
            while b"\n" in raw_buf:
                line_bytes, _, rest = raw_buf.partition(b"\n")
                raw_buf = bytearray(rest)
                if not line_bytes:
                    continue
                t_now = time.perf_counter()
                payload = json.loads(line_bytes)
                t_pub = payload.get("ts", 0.0)
                if t_pub > 0:
                    lat_us = (t_now - t_pub) * 1e6
                    latencies.append(lat_us)
                count += 1
                if count >= num_events:
                    break
            if count >= num_events:
                break
            chunk = s.recv(16384)
            if not chunk:
                break
            raw_buf.extend(chunk)
        s.close()
        consumer_latencies[cid] = latencies
        consumer_counts[cid] = count

    for i in range(num_consumers):
        t = threading.Thread(target=client_worker, args=(i,), daemon=True)
        client_threads.append(t)
        t.start()

    accept_event.wait(timeout=5.0)

    rss_start = get_rss_mb()
    t_cpu_start = time.process_time()
    t0 = time.perf_counter()

    for i in range(1, num_events + 1):
        t_pub = time.perf_counter()
        msg_str = f'{{"type":"TICK","seq":{i},"sym":"BTCUSDT","p":65000.5,"q":1.5,"ts":{t_pub}}}\n'
        frame = msg_str.encode("utf-8")
        for conn in client_conns:
            conn.sendall(frame)

    for conn in client_conns:
        try:
            conn.shutdown(socket.SHUT_RDWR)
            conn.close()
        except Exception:
            pass
    server_sock.close()

    for t in client_threads:
        t.join(timeout=10.0)

    elapsed = time.perf_counter() - t0
    t_cpu = time.process_time() - t_cpu_start
    rss_end = get_rss_mb()

    all_lats = [lat for l_list in consumer_latencies for lat in l_list if lat >= 0]
    all_lats.sort()
    n = len(all_lats) or 1

    return {
        "transport": "TCP",
        "num_consumers": num_consumers,
        "target_events": num_events,
        "received_events_per_consumer": consumer_counts,
        "total_deliveries": sum(consumer_counts),
        "delivery_rate_pct": (sum(consumer_counts) / (num_events * num_consumers)) * 100.0,
        "elapsed_s": round(elapsed, 4),
        "throughput_eps": round((num_events * num_consumers) / elapsed, 1),
        "producer_eps": round(num_events / elapsed, 1),
        "latency_us": {
            "p50": round(all_lats[int(n * 0.50)], 2) if n > 1 else 0.0,
            "p90": round(all_lats[int(n * 0.90)], 2) if n > 1 else 0.0,
            "p95": round(all_lats[int(n * 0.95)], 2) if n > 1 else 0.0,
            "p99": round(all_lats[int(n * 0.99)], 2) if n > 1 else 0.0,
            "p99_9": round(all_lats[int(n * 0.999)], 2) if n > 1 else 0.0,
            "max": round(all_lats[-1], 2) if all_lats else 0.0,
        },
        "cpu_time_s": round(t_cpu, 3),
        "rss_mb": {
            "start": round(rss_start, 2),
            "end": round(rss_end, 2),
            "growth": round(rss_end - rss_start, 2),
        },
    }


def main():
    print("=" * 80)
    print(" MDRAP v6.0.8 Transport Benchmark: Shared Memory (SHM) vs Streaming TCP")
    print("=" * 80)

    results = {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "platform": sys.platform,
        "python": sys.version,
        "shm_scaling": {},
        "tcp_scaling": {},
    }

    events = 50_000
    for consumers in [1, 2, 4]:
        print(f"\n--- Running SHM Benchmark with {consumers} consumer(s) [{events:,} events] ---")
        shm_res = benchmark_shm_scaling(num_events=events, num_consumers=consumers)
        results["shm_scaling"][f"{consumers}_consumers"] = shm_res
        print(f"  Throughput: {shm_res['throughput_eps']:>12,.1f} EPS (Producer: {shm_res['producer_eps']:,.1f} EPS)")
        print(f"  Latency: p50={shm_res['latency_us']['p50']:.1f}us | p95={shm_res['latency_us']['p95']:.1f}us | p99={shm_res['latency_us']['p99']:.1f}us | p99.9={shm_res['latency_us']['p99_9']:.1f}us | max={shm_res['latency_us']['max']:.1f}us")
        print(f"  Delivery: {shm_res['delivery_rate_pct']:.2f}% | CPU: {shm_res['cpu_time_s']:.2f}s | Net RSS: {shm_res['rss_mb']['growth']:.2f}MB")

    for consumers in [1, 2, 4]:
        print(f"\n--- Running TCP Benchmark with {consumers} consumer(s) [{events:,} events] ---")
        tcp_res = benchmark_tcp_scaling(num_events=events, num_consumers=consumers)
        results["tcp_scaling"][f"{consumers}_consumers"] = tcp_res
        print(f"  Throughput: {tcp_res['throughput_eps']:>12,.1f} EPS (Producer: {tcp_res['producer_eps']:,.1f} EPS)")
        print(f"  Latency: p50={tcp_res['latency_us']['p50']:.1f}us | p95={tcp_res['latency_us']['p95']:.1f}us | p99={tcp_res['latency_us']['p99']:.1f}us | p99.9={tcp_res['latency_us']['p99_9']:.1f}us | max={tcp_res['latency_us']['max']:.1f}us")
        print(f"  Delivery: {tcp_res['delivery_rate_pct']:.2f}% | CPU: {tcp_res['cpu_time_s']:.2f}s | Net RSS: {tcp_res['rss_mb']['growth']:.2f}MB")

    out_file = os.path.join("audit", "benchmark", "raw", "transport_benchmark.json")
    with open(out_file, "w") as fp:
        json.dump(results, fp, indent=2)
    print(f"\nAll transport benchmark results saved to {out_file}")


if __name__ == "__main__":
    main()
