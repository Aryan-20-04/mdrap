import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from multicast_arbitrator import (
    ABFeedArbitrator,
    ArbitratorMetrics,
    MulticastFeedSimulator,
    UDPPacket,
)


def test_arbitrator_dual_feed_deduplication():
    arb = ABFeedArbitrator(initial_seq=1)

    # Both Feed A and Feed B arrive for seq 1, 2, 3
    packets = [
        UDPPacket(channel_id="CH1", sequence_num=1, feed_id="A", payload=b"tick_1"),
        UDPPacket(channel_id="CH1", sequence_num=1, feed_id="B", payload=b"tick_1"),
        UDPPacket(channel_id="CH1", sequence_num=2, feed_id="A", payload=b"tick_2"),
        UDPPacket(channel_id="CH1", sequence_num=2, feed_id="B", payload=b"tick_2"),
        UDPPacket(channel_id="CH1", sequence_num=3, feed_id="B", payload=b"tick_3"),
        UDPPacket(channel_id="CH1", sequence_num=3, feed_id="A", payload=b"tick_3"),
    ]

    dispatched = []
    for pkt in packets:
        out = arb.on_packet(pkt)
        dispatched.extend(out)

    assert len(dispatched) == 3
    assert [p.sequence_num for p in dispatched] == [1, 2, 3]

    st = arb.stats()["metrics"]
    assert st["feed_a_packets"] == 3
    assert st["feed_b_packets"] == 3
    assert st["total_received"] == 6
    assert st["dedup_dropped"] == 3
    assert st["in_order_dispatched"] == 3
    assert st["gaps_detected"] == 0
    assert st["tcp_replays_requested"] == 0


def test_arbitrator_single_feed_drop_resilience():
    arb = ABFeedArbitrator(initial_seq=1)

    # Feed A drops seq 2. Feed B delivers seq 2.
    stream = [
        UDPPacket(channel_id="CH1", sequence_num=1, feed_id="A", payload=b"p1"),
        UDPPacket(channel_id="CH1", sequence_num=1, feed_id="B", payload=b"p1"),
        # seq 2 dropped on A! Only B arrives:
        UDPPacket(channel_id="CH1", sequence_num=2, feed_id="B", payload=b"p2"),
        UDPPacket(channel_id="CH1", sequence_num=3, feed_id="A", payload=b"p3"),
        UDPPacket(channel_id="CH1", sequence_num=3, feed_id="B", payload=b"p3"),
    ]

    dispatched = []
    for pkt in stream:
        dispatched.extend(arb.on_packet(pkt))

    # All 3 packets arrive cleanly with 0 gaps and 0 replays
    assert len(dispatched) == 3
    assert [p.sequence_num for p in dispatched] == [1, 2, 3]
    assert arb.metrics.gaps_detected == 0
    assert arb.metrics.tcp_replays_requested == 0


def test_arbitrator_dual_feed_drop_tcp_recovery():
    # Setup simulator to act as TCP Replay archive
    sim = MulticastFeedSimulator(channel_id="CH1", drop_rate_a=0.0, drop_rate_b=0.0)
    # Preload simulator history with 5 packets
    for i in range(1, 6):
        sim.publish_event(f"event_{i}".encode("utf-8"))

    arb = ABFeedArbitrator(
        tcp_replay_client=sim.tcp_replay_request,
        initial_seq=1,
    )

    # Both A and B drop packet 3!
    # Stream delivers: seq 1, seq 2, then JUMP to seq 4, then seq 5
    stream = [
        UDPPacket(channel_id="CH1", sequence_num=1, feed_id="A", payload=b"event_1"),
        UDPPacket(channel_id="CH1", sequence_num=2, feed_id="A", payload=b"event_2"),
        # Seq 3 missing on both lines!
        UDPPacket(channel_id="CH1", sequence_num=4, feed_id="A", payload=b"event_4"),
        UDPPacket(channel_id="CH1", sequence_num=5, feed_id="A", payload=b"event_5"),
    ]

    dispatched = []
    for pkt in stream:
        dispatched.extend(arb.on_packet(pkt))

    # When seq 4 arrived, arbitrator detected gap [3..3], invoked TCP replay, recovered seq 3,
    # and drained both 3 and 4!
    assert len(dispatched) == 5
    assert [p.sequence_num for p in dispatched] == [1, 2, 3, 4, 5]

    st = arb.stats()["metrics"]
    assert st["gaps_detected"] >= 1
    assert st["tcp_replays_requested"] == 1
    assert st["tcp_packets_recovered"] == 1
    assert st["in_order_dispatched"] == 5


def test_arbitrator_multi_channel():
    arb = ABFeedArbitrator(initial_seq=1)

    p1_eq = UDPPacket(channel_id="EQUITY", sequence_num=1, feed_id="A", payload=b"AAPL")
    p1_fx = UDPPacket(channel_id="FOREX", sequence_num=1, feed_id="A", payload=b"EURUSD")
    p2_eq = UDPPacket(channel_id="EQUITY", sequence_num=2, feed_id="A", payload=b"MSFT")

    d1 = arb.on_packet(p1_eq)
    d2 = arb.on_packet(p1_fx)
    d3 = arb.on_packet(p2_eq)

    assert len(d1) == 1 and d1[0].channel_id == "EQUITY"
    assert len(d2) == 1 and d2[0].channel_id == "FOREX"
    assert len(d3) == 1 and d3[0].channel_id == "EQUITY"

    channels = arb.stats()["channels"]
    assert channels["EQUITY"]["expected_seq"] == 3
    assert channels["FOREX"]["expected_seq"] == 2


def test_multicast_feed_simulator_end_to_end():
    # 5% packet drop on Feed A, 5% packet drop on Feed B
    sim = MulticastFeedSimulator(channel_id="SIM1", drop_rate_a=0.05, drop_rate_b=0.05, seed=12345)
    arb = ABFeedArbitrator(
        tcp_replay_client=sim.tcp_replay_request,
        initial_seq=1,
    )

    total_events = 200
    dispatched_packets = []

    for i in range(1, total_events + 1):
        pa, pb = sim.publish_event(f"tick_payload_{i}".encode("utf-8"))
        if pa is not None:
            dispatched_packets.extend(arb.on_packet(pa))
        if pb is not None:
            dispatched_packets.extend(arb.on_packet(pb))

    # All 200 events must be recovered and dispatched in exact sequence
    assert len(dispatched_packets) == total_events
    for idx, pkt in enumerate(dispatched_packets, start=1):
        assert pkt.sequence_num == idx
        assert pkt.payload == f"tick_payload_{idx}".encode("utf-8")

    st = arb.stats()["metrics"]
    assert st["in_order_dispatched"] == total_events
    # Deduplication worked on surviving packets
    assert st["dedup_dropped"] > 0
