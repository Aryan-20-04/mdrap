"""
MDRAP v5.1 — Network Capability Detection & Hardware Socket Test Suite (Initiative 7).

Verifies:
1. detect_network_capabilities() reports accurate OS and network acceleration stack.
2. STANDARD_SOCKET is universally available as portable baseline.
3. AFXDPConfig and DPDKConfig institutional blueprints.
4. HighSpeedSocketReceiver non-blocking bind and cleanup.
"""

import socket
import sys
import pytest

from mdrap.network import (
    AFXDPConfig,
    DPDKConfig,
    HighSpeedSocketReceiver,
    NetworkBackendType,
    NetworkCapabilities,
    detect_network_capabilities,
)


class TestNetworkDetector:
    """Verifies network capability detection and socket ingress tuning."""

    def test_detect_network_capabilities_host(self):
        """Verify network capability detector correctly profiles the host environment."""
        caps = detect_network_capabilities()

        assert isinstance(caps, NetworkCapabilities)
        assert caps.os_name == sys.platform
        assert isinstance(caps.kernel_version, str)
        assert NetworkBackendType.STANDARD_SOCKET in caps.supported_backends
        assert isinstance(caps.recommended_backend, NetworkBackendType)

        if sys.platform == "win32":
            assert caps.has_iocp is True
            assert caps.has_rio is True
            assert NetworkBackendType.WINDOWS_IOCP in caps.supported_backends
            assert NetworkBackendType.WINDOWS_RIO in caps.supported_backends
            assert caps.has_af_xdp is False

    def test_af_xdp_config_blueprint(self):
        """Verify Linux AF_XDP blueprint configuration properties."""
        cfg = AFXDPConfig(
            interface_name="eth1",
            queue_id=2,
            umem_frame_size=4096,
            zero_copy=True,
            pinned_core=4,
        )
        assert cfg.interface_name == "eth1"
        assert cfg.queue_id == 2
        assert cfg.umem_frame_size == 4096
        assert cfg.zero_copy is True
        assert cfg.pinned_core == 4

    def test_dpdk_config_blueprint(self):
        """Verify DPDK poll-mode driver configuration properties."""
        cfg = DPDKConfig(
            port_id=1,
            rx_ring_size=8192,
            mempool_name="custom_pool",
            hugepage_mount="/mnt/huge_1g",
            isolated_core=6,
        )
        assert cfg.port_id == 1
        assert cfg.rx_ring_size == 8192
        assert cfg.mempool_name == "custom_pool"
        assert cfg.hugepage_mount == "/mnt/huge_1g"
        assert cfg.isolated_core == 6

    def test_high_speed_socket_receiver_lifecycle(self):
        """Verify socket receiver binds non-blocking socket with tuned buffer and closes cleanly."""
        # Find an open port
        sock_test = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock_test.bind(("127.0.0.1", 0))
        test_port = sock_test.getsockname()[1]
        sock_test.close()

        receiver = HighSpeedSocketReceiver(host="127.0.0.1", port=test_port)
        try:
            receiver.bind()
            assert receiver.sock is not None
            # Non-blocking receive should immediately return None
            assert receiver.recv_packet() is None

            # Send test UDP datagram
            sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sender.sendto(b"TEST_MARKET_TICK", ("127.0.0.1", test_port))
                # Receive packet
                import time
                deadline = time.time() + 1.0
                packet = None
                while time.time() < deadline:
                    packet = receiver.recv_packet()
                    if packet is not None:
                        break
                    time.sleep(0.001)

                assert packet is not None
                data, addr, recv_ts = packet
                assert data == b"TEST_MARKET_TICK"
                assert addr[0] == "127.0.0.1"
                assert recv_ts > 0.0
            finally:
                sender.close()
        finally:
            receiver.close()
            assert receiver.sock is None
