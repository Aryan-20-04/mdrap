"""Unit tests for Linux AF_XDP / Kernel Bypass Network Adapter & UMEM Ring Buffers."""

import pytest
from mdrap.af_xdp import (
    KernelBypassReceiver,
    UMEMConfig,
    VirtualUMEM,
    XdpPacketDesc,
)


def test_umem_config_defaults_and_bytes():
    """Verify UMEMConfig defaults and total memory calculation."""
    cfg = UMEMConfig(chunk_size=4096, chunk_count=1024)
    assert cfg.total_bytes == 4096 * 1024
    assert cfg.zero_copy is True
    assert cfg.need_wakeup is True


def test_virtual_umem_allocation_and_release():
    """Verify VirtualUMEM allocates and releases chunk addresses correctly."""
    cfg = UMEMConfig(chunk_size=2048, chunk_count=4)
    umem = VirtualUMEM(cfg)

    assert len(umem.free_chunks) == 4
    c1 = umem.allocate_chunk()
    c2 = umem.allocate_chunk()
    c3 = umem.allocate_chunk()
    c4 = umem.allocate_chunk()
    assert all(isinstance(c, int) for c in (c1, c2, c3, c4))
    assert len(umem.free_chunks) == 0

    # Exhausted allocation returns None
    assert umem.allocate_chunk() is None

    # Write and read back data
    test_data = b"MDRAP_SBE_PACKET_TEST"
    length = umem.write_packet(c1, test_data)
    assert length == len(test_data)

    read_view = umem.read_packet(c1, length)
    assert bytes(read_view) == test_data

    # Releasing chunks makes them available again
    umem.release_chunk(c1)
    assert len(umem.free_chunks) == 1
    assert umem.allocate_chunk() == c1


def test_kernel_bypass_receiver_batch_poll_and_callback():
    """Verify KernelBypassReceiver dispatches injected packets to callback and recycles chunks."""
    received = []

    def handle_pkt(data: memoryview, ts: float):
        received.append((bytes(data), ts))

    receiver = KernelBypassReceiver(ifname="eth0", on_packet=handle_pkt)

    pkt1 = b"PACKET_1_BINANCE_BBO"
    pkt2 = b"PACKET_2_NASDAQ_ITCH"

    assert receiver.inject_test_packet(pkt1) is True
    assert receiver.inject_test_packet(pkt2) is True

    polled = receiver.poll_batch(max_packets=10)
    assert polled == 2
    assert len(received) == 2
    assert received[0][0] == pkt1
    assert received[1][0] == pkt2

    stats = receiver.get_stats()
    assert stats["rx_packets"] == 2
    assert stats["rx_bytes"] == len(pkt1) + len(pkt2)
    assert stats["rx_dropped"] == 0
    assert stats["batch_reads"] == 1
    assert stats["free_umem_chunks"] == receiver.config.chunk_count


def test_kernel_bypass_receiver_chunk_exhaustion():
    """Verify that when UMEM is starved of free chunks, drops and starvation metrics increment."""
    cfg = UMEMConfig(chunk_size=1024, chunk_count=2)
    receiver = KernelBypassReceiver(umem_config=cfg)

    # Inject 2 packets to fill the 2 chunks
    assert receiver.inject_test_packet(b"PKT1") is True
    assert receiver.inject_test_packet(b"PKT2") is True

    # 3rd packet must fail and record drop
    assert receiver.inject_test_packet(b"PKT3") is False

    stats = receiver.get_stats()
    assert stats["rx_dropped"] == 1
    assert stats["fill_ring_starved"] == 1
