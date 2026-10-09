"""
MDRAP Phase 8 Baseline Wire-to-Ingress Socket Benchmark.
Measures baseline standard OS network socket ingress latency (TCP and UDP)
to establish the reference point for kernel-bypass feasibility evaluation.
"""

import sys
import os
import time
import json
import socket
import statistics
import threading


def measure_tcp_loopback_latency(num_packets=10000, packet_size=64):
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    server_sock.bind(("127.0.0.1", 0))
    server_sock.listen(1)
    port = server_sock.getsockname()[1]

    client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    client_sock.connect(("127.0.0.1", port))

    conn, _ = server_sock.accept()
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    payload = b"X" * packet_size
    latencies_us = []

    # Measurement loop
    for _ in range(num_packets):
        t0 = time.perf_counter_ns()
        client_sock.sendall(payload)
        chunk = conn.recv(packet_size)
        t1 = time.perf_counter_ns()
        latencies_us.append((t1 - t0) / 1000.0)

    client_sock.close()
    conn.close()
    server_sock.close()

    latencies_us.sort()
    n = len(latencies_us)
    return {
        "transport": "TCP_LOOPBACK",
        "packet_size_bytes": packet_size,
        "packets_tested": num_packets,
        "latency_us": {
            "p50": round(latencies_us[int(n * 0.50)], 2),
            "p90": round(latencies_us[int(n * 0.90)], 2),
            "p95": round(latencies_us[int(n * 0.95)], 2),
            "p99": round(latencies_us[int(n * 0.99)], 2),
            "p99_9": round(latencies_us[int(n * 0.999)], 2),
            "mean": round(statistics.mean(latencies_us), 2),
            "min": round(min(latencies_us), 2),
            "max": round(max(latencies_us), 2),
        },
    }


def measure_udp_loopback_latency(num_packets=10000, packet_size=64):
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server_sock.bind(("127.0.0.1", 0))
    port = server_sock.getsockname()[1]

    client_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    payload = b"U" * packet_size
    latencies_us = []

    for _ in range(num_packets):
        t0 = time.perf_counter_ns()
        client_sock.sendto(payload, ("127.0.0.1", port))
        data, _ = server_sock.recvfrom(packet_size)
        t1 = time.perf_counter_ns()
        latencies_us.append((t1 - t0) / 1000.0)

    client_sock.close()
    server_sock.close()

    latencies_us.sort()
    n = len(latencies_us)
    return {
        "transport": "UDP_LOOPBACK",
        "packet_size_bytes": packet_size,
        "packets_tested": num_packets,
        "latency_us": {
            "p50": round(latencies_us[int(n * 0.50)], 2),
            "p90": round(latencies_us[int(n * 0.90)], 2),
            "p95": round(latencies_us[int(n * 0.95)], 2),
            "p99": round(latencies_us[int(n * 0.99)], 2),
            "p99_9": round(latencies_us[int(n * 0.999)], 2),
            "mean": round(statistics.mean(latencies_us), 2),
            "min": round(min(latencies_us), 2),
            "max": round(max(latencies_us), 2),
        },
    }


def run_ingress_benchmarks():
    print("=== MDRAP Phase 8 Baseline Ingress Socket Latency Benchmark ===")
    results = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host_environment": {
            "os": "Windows 11 Enterprise x86_64",
            "python_runtime": "3.13.1",
            "socket_implementation": "Winsock2 standard kernel stack",
            "hardware_bypass_available": False,
        },
        "measurements": [],
    }

    tcp_64 = measure_tcp_loopback_latency(10000, 64)
    print(f"TCP 64B:  p50={tcp_64['latency_us']['p50']}s | p99={tcp_64['latency_us']['p99']}s | max={tcp_64['latency_us']['max']}s")
    results["measurements"].append(tcp_64)

    tcp_256 = measure_tcp_loopback_latency(10000, 256)
    print(f"TCP 256B: p50={tcp_256['latency_us']['p50']}s | p99={tcp_256['latency_us']['p99']}s | max={tcp_256['latency_us']['max']}s")
    results["measurements"].append(tcp_256)

    udp_64 = measure_udp_loopback_latency(10000, 64)
    print(f"UDP 64B:  p50={udp_64['latency_us']['p50']}s | p99={udp_64['latency_us']['p99']}s | max={udp_64['latency_us']['max']}s")
    results["measurements"].append(udp_64)

    out_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "audit",
        "phase8",
        "hardware_benchmark_results.json",
    )
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\n[OK] Saved hardware ingress benchmark results to: {out_path}")
    return results


if __name__ == "__main__":
    run_ingress_benchmarks()
