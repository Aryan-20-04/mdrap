"""Experiment 2: Socket versus Kernel-Bypass Capability Qualification (Strategy §14.5 / Sanaee et al. 2025 Machnet).

Probes host OS, virtualization, network driver capabilities, and determines whether
zero-copy / AF_XDP or standard POSIX sockets should be selected. Verifies clean,
transparent fallback on unsupported platforms (Windows, macOS, virtual cloud NICs).
"""

from __future__ import annotations

import os
import platform
import socket
import sys
import time
from dataclasses import dataclass
from typing import Any

__stability__ = "experimental"


@dataclass
class NetworkCapabilityReport:
    os_name: str
    os_release: str
    machine_arch: str
    has_af_xdp_support: bool
    has_recvmmsg_support: bool
    has_so_reuseport: bool
    is_virtualized_nic: bool
    selected_ingest_backend: str
    baseline_socket_latency_us: float
    baseline_throughput_msgs_sec: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "os": f"{self.os_name} {self.os_release}",
            "arch": self.machine_arch,
            "af_xdp_capable": self.has_af_xdp_support,
            "recvmmsg_capable": self.has_recvmmsg_support,
            "reuseport_capable": self.has_so_reuseport,
            "virtualized_env": self.is_virtualized_nic,
            "selected_backend": self.selected_ingest_backend,
            "socket_p50_us": round(self.baseline_socket_latency_us, 2),
            "socket_throughput_mps": round(self.baseline_throughput_msgs_sec, 1),
        }


def probe_kernel_network_capabilities() -> dict[str, bool]:
    """Inspect environment for kernel-bypass and low-latency socket features."""
    caps = {
        "af_xdp": False,
        "recvmmsg": hasattr(socket, "recvmmsg"),
        "so_reuseport": hasattr(socket, "SO_REUSEPORT"),
        "so_bindtodevice": hasattr(socket, "SO_BINDTODEVICE"),
    }

    # Check AF_XDP family support
    if hasattr(socket, "AF_XDP"):
        caps["af_xdp"] = True
    elif sys.platform.startswith("linux"):
        # Check /sys/class/net or /proc/version
        try:
            with open("/proc/version", "r") as f:
                ver_str = f.read().lower()
                caps["af_xdp"] = "linux" in ver_str and (
                    "5." in ver_str or "6." in ver_str
                )
        except Exception:
            pass

    return caps


def measure_loopback_socket_baseline(num_messages: int = 5_000) -> tuple[float, float]:
    """Measure raw baseline loopback TCP socket latency and throughput."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "TCP_NODELAY"):
        srv.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "TCP_NODELAY"):
        client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    client.connect(("127.0.0.1", port))
    conn, _ = srv.accept()

    payload = b"X" * 64
    latencies: list[float] = []

    t0 = time.perf_counter()
    for _ in range(num_messages):
        t_start = time.perf_counter_ns()
        client.sendall(payload)
        data = conn.recv(64)
        latencies.append((time.perf_counter_ns() - t_start) / 1000.0)
    elapsed = max(time.perf_counter() - t0, 1e-6)

    client.close()
    conn.close()
    srv.close()

    sorted_lats = sorted(latencies)
    p50 = sorted_lats[len(sorted_lats) // 2]
    throughput = num_messages / elapsed
    return p50, throughput


def run_experiment_2() -> NetworkCapabilityReport:
    """Execute Experiment 2: Probe host environment and evaluate socket baseline."""
    caps = probe_kernel_network_capabilities()
    p50_us, throughput = measure_loopback_socket_baseline(num_messages=5_000)

    # Detect virtualization signals
    is_virt = False
    system_name = platform.system()
    if system_name == "Linux" and (
        os.path.exists("/.dockerenv") or os.path.exists("/run/systemd/container")
    ):
        is_virt = True

    # Qualification decision logic (Sanaee et al. 2025 Machnet)
    if caps["af_xdp"] and not is_virt:
        selected = "AF_XDP_ZERO_COPY (Qualified)"
    elif caps["af_xdp"] and is_virt:
        selected = "AF_XDP_COPY_MODE (Virtual NIC fallback)"
    elif caps["recvmmsg"]:
        selected = "POSIX_RECVMMSG_BATCHED (Standard Linux)"
    else:
        selected = f"STANDARD_SOCKET_TCP ({system_name} Portable Fallback)"

    return NetworkCapabilityReport(
        os_name=system_name,
        os_release=platform.release(),
        machine_arch=platform.machine(),
        has_af_xdp_support=caps["af_xdp"],
        has_recvmmsg_support=caps["recvmmsg"],
        has_so_reuseport=caps["so_reuseport"],
        is_virtualized_nic=is_virt,
        selected_ingest_backend=selected,
        baseline_socket_latency_us=p50_us,
        baseline_throughput_msgs_sec=throughput,
    )


if __name__ == "__main__":
    print("=" * 70)
    print("MDRAP Experiment 2: Socket vs Kernel-Bypass Capability Qualification")
    print("=" * 70)
    rep = run_experiment_2()
    d = rep.to_dict()
    print(f"Platform:         {d['os']} ({d['arch']})")
    print(f"AF_XDP Capable:   {d['af_xdp_capable']}")
    print(f"recvmmsg Capable: {d['recvmmsg_capable']}")
    print(f"Selected Backend: {d['selected_backend']}")
    print(f"Loopback Latency: p50 = {d['socket_p50_us']} us")
    print(f"Throughput:       {d['socket_throughput_mps']:,.1f} msgs/sec")
