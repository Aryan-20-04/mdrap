"""
MDRAP Phase 6 — Native Networking Architecture & Hardware Capability Detection (Initiative 7).

Provides:
1. Cross-platform detection of kernel-bypass networking (Linux AF_XDP, DPDK, recvmmsg, Win32 IOCP/RIO).
2. Architectural configuration blueprints for institutional Linux AF_XDP and DPDK bare-metal feeds.
3. High-throughput portable socket receiver with zero-drop buffer tuning.
"""

from __future__ import annotations

__stability__ = "stable"

import dataclasses
from enum import Enum
import logging
import os
import platform
import socket
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("mdrap.network")


class NetworkBackendType(str, Enum):
    """Network ingress backend architecture types."""

    STANDARD_SOCKET = "STANDARD_SOCKET"  # Portable POSIX / Windows BSD sockets
    LINUX_AF_XDP = "LINUX_AF_XDP"  # Linux 5.4+ eXpress Data Path zero-copy
    LINUX_DPDK = "LINUX_DPDK"  # Data Plane Development Kit PMD
    WINDOWS_IOCP = "WINDOWS_IOCP"  # Windows I/O Completion Ports
    WINDOWS_RIO = "WINDOWS_RIO"  # Windows Registered I/O


@dataclasses.dataclass(frozen=True)
class NetworkCapabilities:
    """Hardware and OS network acceleration capability profile."""

    os_name: str
    kernel_version: str
    has_af_xdp: bool
    has_dpdk: bool
    has_recvmmsg: bool
    has_iocp: bool
    has_rio: bool
    so_busy_poll: bool
    supported_backends: List[NetworkBackendType]
    recommended_backend: NetworkBackendType


@dataclasses.dataclass(frozen=True)
class AFXDPConfig:
    """Institutional blueprint configuration for Linux AF_XDP kernel-bypass ingress."""

    interface_name: str = "eth0"
    queue_id: int = 0
    umem_frame_size: int = 4096
    umem_num_frames: int = 65536
    zero_copy: bool = True
    busy_poll_us: int = 20
    need_wakeup: bool = True
    pinned_core: Optional[int] = None


@dataclasses.dataclass(frozen=True)
class DPDKConfig:
    """Configuration blueprint for DPDK userspace poll-mode drivers."""

    port_id: int = 0
    rx_rings: int = 1
    rx_ring_size: int = 4096
    mempool_name: str = "mdrap_mbuf_pool"
    mempool_cache_size: int = 512
    hugepage_mount: str = "/mnt/huge"
    isolated_core: Optional[int] = None


def detect_network_capabilities() -> NetworkCapabilities:
    """Inspect host operating system and network stack acceleration features."""
    os_name = sys.platform
    kernel_version = platform.release()

    has_af_xdp = False
    has_dpdk = False
    has_recvmmsg = hasattr(socket, "recvmmsg") or hasattr(os, "recvmmsg")
    has_iocp = os_name == "win32"
    has_rio = os_name == "win32"
    so_busy_poll = hasattr(socket, "SO_BUSY_POLL")

    supported: List[NetworkBackendType] = [NetworkBackendType.STANDARD_SOCKET]

    # Linux-specific capability detection
    if os_name.startswith("linux"):
        # AF_XDP requires Linux kernel >= 5.4 and AF_XDP constant
        try:
            k_major, k_minor = (int(x) for x in kernel_version.split(".")[:2])
            if (k_major > 5) or (k_major == 5 and k_minor >= 4):
                if hasattr(socket, "AF_XDP") or hasattr(socket, "SOL_XDP"):
                    has_af_xdp = True
                    supported.append(NetworkBackendType.LINUX_AF_XDP)
        except Exception:
            pass

        # DPDK requires hugepage filesystem support
        if os.path.exists("/sys/kernel/mm/hugepages"):
            has_dpdk = True
            supported.append(NetworkBackendType.LINUX_DPDK)

    elif os_name == "win32":
        supported.append(NetworkBackendType.WINDOWS_IOCP)
        supported.append(NetworkBackendType.WINDOWS_RIO)

    # Determine recommended backend based on verified acceleration
    if NetworkBackendType.LINUX_AF_XDP in supported:
        recommended = NetworkBackendType.LINUX_AF_XDP
    elif NetworkBackendType.WINDOWS_RIO in supported:
        recommended = NetworkBackendType.WINDOWS_RIO
    else:
        recommended = NetworkBackendType.STANDARD_SOCKET

    return NetworkCapabilities(
        os_name=os_name,
        kernel_version=kernel_version,
        has_af_xdp=has_af_xdp,
        has_dpdk=has_dpdk,
        has_recvmmsg=has_recvmmsg,
        has_iocp=has_iocp,
        has_rio=has_rio,
        so_busy_poll=so_busy_poll,
        supported_backends=supported,
        recommended_backend=recommended,
    )


class HighSpeedSocketReceiver:
    """Portable, tuned UDP socket receiver for high-frequency feed ingress."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9002,
        buffer_size_bytes: int = 16 * 1024 * 1024,  # 16 MB OS buffer
        non_blocking: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.buffer_size_bytes = buffer_size_bytes
        self.non_blocking = non_blocking
        self.sock: Optional[socket.socket] = None
        self._running = False

    def bind(self) -> None:
        """Bind socket and apply high-performance options."""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # Apply large OS socket receive buffer to prevent packet drops during microbursts
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self.buffer_size_bytes)
        except Exception as e:
            logger.debug("Could not set SO_RCVBUF to %d: %s", self.buffer_size_bytes, e)

        # Linux busy polling if available
        if hasattr(socket, "SO_BUSY_POLL"):
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BUSY_POLL, 20)
            except Exception:
                pass

        if self.non_blocking:
            s.setblocking(False)

        s.bind((self.host, self.port))
        self.sock = s
        self._running = True

    def close(self) -> None:
        """Close socket cleanly."""
        self._running = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def recv_packet(
        self, max_bytes: int = 65536
    ) -> Optional[Tuple[bytes, Tuple[str, int], float]]:
        """Non-blocking single packet receive.

        Returns (payload_bytes, (host, port), receive_ts) or None if no packet available.
        """
        if not self.sock:
            return None
        try:
            data, addr = self.sock.recvfrom(max_bytes)
            recv_ts = time.time()
            return data, addr, recv_ts
        except (BlockingIOError, InterruptedError):
            return None
        except Exception as e:
            logger.error("Error receiving from socket: %s", e)
            return None
