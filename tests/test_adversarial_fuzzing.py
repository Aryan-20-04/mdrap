"""
Adversarial Binary Fuzzing Suite (v2.6 Reliability Milestone).

Executes 100,000+ deterministic mutated byte streams against:
1. ITCHParser (NASDAQ TotalView-ITCH 5.0)
2. BinaryStreamParser (MDRAP-BIN V1, V2 & Depth framing)
3. MoldUDP64Dissector (Multicast UDP packet framing)
4. ITCHOrderBookTracker (End-to-end state machine fuzzing)

Verifies:
- ZERO crashes, unhandled exceptions, memory panics, or infinite loops.
- Corrupted frames are safely dropped or ignored without leaking resources.
- Deterministic reproducibility using fixed seed=42.
"""

from __future__ import annotations

import os
import random
import struct
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from itch import (
    ITCHParser,
    ITCHOrderBookTracker,
    MSG_ADD_ORDER,
    MSG_ORDER_EXECUTED,
    MSG_ORDER_CANCEL,
    MSG_ORDER_DELETE,
    MSG_ORDER_REPLACE,
    MSG_TRADE_NON_CROSS,
    MSG_ADD_ORDER_MPID,
    MSG_ORDER_EXECUTED_PRICE,
    MSG_TRADE_CROSS,
    MSG_SYSTEM_EVENT,
    MSG_STOCK_DIRECTORY,
    MSG_STOCK_TRADING_ACTION,
    MSG_BROKEN_TRADE,
    MSG_NOII,
    STRUCT_A,
    STRUCT_E,
    STRUCT_X,
    STRUCT_D,
    STRUCT_U,
    STRUCT_P,
    STRUCT_F,
    STRUCT_C,
    STRUCT_Q,
    STRUCT_S,
    STRUCT_R,
    STRUCT_H,
    STRUCT_B,
    STRUCT_I,
)
from protocol import (
    BinaryStreamParser,
    pack_tick_frame,
    pack_tick_frame_v2,
    pack_depth_frame,
    MAGIC,
)
from pcap import MoldUDP64Dissector, MOLDUDP64_HEADER_STRUCT


def _mutate_bytes(data: bytes, rng: random.Random) -> bytes:
    """Apply adversarial byte mutations: bitflips, truncations, insertions, injections."""
    if not data:
        return rng.randbytes(rng.randint(1, 64))

    b = bytearray(data)
    mutation_type = rng.randint(0, 5)

    if mutation_type == 0:
        # Bitflip
        idx = rng.randint(0, len(b) - 1)
        b[idx] ^= 1 << rng.randint(0, 7)
    elif mutation_type == 1:
        # Byte replacement with boundary values (0x00, 0xFF, 0x80, 0x7F)
        idx = rng.randint(0, len(b) - 1)
        b[idx] = rng.choice([0x00, 0xFF, 0x80, 0x7F, rng.randint(0, 255)])
    elif mutation_type == 2:
        # Truncation
        cut = rng.randint(0, len(b) - 1)
        b = b[:cut]
    elif mutation_type == 3:
        # Injection / expansion
        idx = rng.randint(0, len(b))
        b[idx:idx] = rng.randbytes(rng.randint(1, 16))
    elif mutation_type == 4:
        # Swap two bytes
        if len(b) >= 2:
            i, j = rng.sample(range(len(b)), 2)
            b[i], b[j] = b[j], b[i]
    else:
        # Total random replacement
        b = bytearray(rng.randbytes(rng.randint(0, len(data) * 2)))

    return bytes(b)


class TestITCHParserAdversarialFuzzing:
    """Fuzzing the ITCH TotalView binary parser."""

    def test_fuzz_itch_parser_valid_seeds_mutated(self):
        """Generate valid ITCH seeds across all types, mutate heavily, verify zero crashes."""
        rng = random.Random(42)

        # Build baseline valid seed frames for all 14 types
        valid_seeds = [
            (MSG_ADD_ORDER, STRUCT_A.pack(1, 1, b"\x00" * 6, 1001, b"B", 100, b"AAPL    ", 1500000)),
            (MSG_ORDER_EXECUTED, STRUCT_E.pack(1, 1, b"\x00" * 6, 1001, 50, 9999)),
            (MSG_ORDER_CANCEL, STRUCT_X.pack(1, 1, b"\x00" * 6, 1001, 25)),
            (MSG_ORDER_DELETE, STRUCT_D.pack(1, 1, b"\x00" * 6, 1001)),
            (MSG_ORDER_REPLACE, STRUCT_U.pack(1, 1, b"\x00" * 6, 1001, 1002, 100, 1510000)),
            (MSG_TRADE_NON_CROSS, STRUCT_P.pack(1, 1, b"\x00" * 6, 1001, b"B", 100, b"AAPL    ", 1500000, 9999)),
            (MSG_ADD_ORDER_MPID, STRUCT_F.pack(1, 1, b"\x00" * 6, 1001, b"B", 100, b"AAPL    ", 1500000, b"GSCO")),
            (MSG_ORDER_EXECUTED_PRICE, STRUCT_C.pack(1, 1, b"\x00" * 6, 1001, 50, 9999, b"Y", 1500000)),
            (MSG_TRADE_CROSS, STRUCT_Q.pack(1, 1, b"\x00" * 6, 500, b"AAPL    ", 1500000, 9999, b"O")),
            (MSG_SYSTEM_EVENT, STRUCT_S.pack(1, 1, b"\x00" * 6, b"O")),
            (MSG_STOCK_DIRECTORY, STRUCT_R.pack(1, 0, b"\x00" * 6, b"AAPL    ", b"Q", b"N", 100, b"N", b"C", b"  ", b"Y", b"N", b"N", b"N", b"N", 1, b"N")),
            (MSG_STOCK_TRADING_ACTION, STRUCT_H.pack(1, 1, b"\x00" * 6, b"AAPL    ", b"T", b" ", b"    ")),
            (MSG_BROKEN_TRADE, STRUCT_B.pack(1, 1, b"\x00" * 6, 9999)),
            (MSG_NOII, STRUCT_I.pack(1, 1, b"\x00" * 6, 1000, 200, b"B", b"AAPL    ", 1510000, 1500000, 1505000, b"O", b" ")),
        ]

        # Run 30,000 mutations across ITCH message types
        for i in range(30_000):
            msg_type, seed_body = rng.choice(valid_seeds)
            mutated = _mutate_bytes(seed_body, rng)

            # May also randomly corrupt the msg_type byte
            fuzzed_type = msg_type if (i % 5 != 0) else rng.randbytes(1)

            # Must NEVER raise unhandled exception
            result = ITCHParser.parse_payload(fuzzed_type, mutated)
            if result is not None:
                assert hasattr(result, "msg_type")

    def test_fuzz_itch_completely_random_blobs(self):
        """Pass 20,000 completely random byte buffers with random lengths into ITCHParser."""
        rng = random.Random(1337)
        for _ in range(20_000):
            type_b = rng.randbytes(1)
            body_len = rng.randint(0, 128)
            blob = rng.randbytes(body_len)
            ITCHParser.parse_payload(type_b, blob)


class TestBinaryStreamParserAdversarialFuzzing:
    """Fuzzing the binary wire protocol stream deserializer."""

    def test_fuzz_binary_stream_parser_mutated_frames(self):
        """Feed 30,000 mutated binary wire protocol chunks to BinaryStreamParser."""
        rng = random.Random(999)
        parser = BinaryStreamParser()

        # Generate valid sample frames
        v1_frame = pack_tick_frame(1, "BTC/USD", "BINANCE", 50000.0, 1.0, 49990.0, 50010.0, "VALID", False, 1000.0, 1000.001, 1000.002, 12.0)
        v2_frame = pack_tick_frame_v2(2, "ETH/USDT", "KRAKEN", 3000.0, 2.0, 2999.0, 3001.0, "VALID", False, 1000.0, 1000.001, 1000.002, 8.0, 0)
        depth_frame = pack_depth_frame(3, "SOL/USD", 150.0, 150.2, 10.0, 12.0, 150.1, 5.0, False, 1000.0, 1000.001, 1000.002, 15.0)

        seeds = [v1_frame, v2_frame, depth_frame]

        for i in range(30_000):
            seed = rng.choice(seeds)
            mutated = _mutate_bytes(seed, rng)

            # Chunk into random piece sizes (1 to full len)
            chunk_size = rng.randint(1, max(1, len(mutated)))
            for offset in range(0, len(mutated), chunk_size):
                chunk = mutated[offset : offset + chunk_size]
                events = parser.feed(chunk)
                for ev in events:
                    assert isinstance(ev, dict)

    def test_fuzz_binary_stream_noise_injection(self):
        """Inject 10,000 chunks of random noise and corrupted headers into parser."""
        rng = random.Random(888)
        parser = BinaryStreamParser()

        for _ in range(10_000):
            noise_len = rng.randint(0, 256)
            noise = rng.randbytes(noise_len)
            events = parser.feed(noise)
            assert isinstance(events, list)


class TestMoldUDP64DissectorAdversarialFuzzing:
    """Fuzzing the MoldUDP64 multicast dissector."""

    def test_fuzz_moldudp64_dissector(self):
        """Feed 20,000 mutated MoldUDP64 datagrams to dissector."""
        rng = random.Random(777)

        # Baseline valid MoldUDP64 packet: 10-byte session + 8-byte seq + 2-byte count
        sess = b"MDRAP_SESS"
        seq = 100
        count = 2
        msg1 = b"A" + b"\x00" * 35
        msg2 = b"E" + b"\x00" * 30
        hdr = MOLDUDP64_HEADER_STRUCT.pack(sess, seq, count)
        valid_packet = hdr + struct.pack("!H", len(msg1)) + msg1 + struct.pack("!H", len(msg2)) + msg2

        for _ in range(20_000):
            mutated = _mutate_bytes(valid_packet, rng)
            s_id, s_num, msgs = MoldUDP64Dissector.dissect(mutated)
            assert isinstance(s_id, str)
            assert isinstance(s_num, int)
            assert isinstance(msgs, list)


class TestEndToEndFuzzingWithOrderBookTracker:
    """End-to-end integration: feed fuzzed MoldUDP64 packets into ITCHOrderBookTracker."""

    def test_fuzz_dissect_and_order_book_reconstruction(self):
        """Verify that parsed/fuzzed messages routed into ITCHOrderBookTracker never crash."""
        rng = random.Random(555)
        tracker = ITCHOrderBookTracker()

        for _ in range(5_000):
            # Synthesize random packet
            blob = rng.randbytes(rng.randint(10, 200))
            _, _, msgs = MoldUDP64Dissector.dissect(blob)
            for m in msgs:
                if len(m) >= 1:
                    msg_type = m[:1]
                    body = m[1:]
                    parsed = ITCHParser.parse_payload(msg_type, body)
                    if parsed:
                        tracker.process_message(parsed)
