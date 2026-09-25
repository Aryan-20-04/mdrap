"""
Fuzz Testing for Canonical Data Models (Phase 5).

Strict Requirement:
No random or malformed event may crash the parser or pipeline process.
All payloads must either successfully parse into a valid/invalid MarketEvent
or return an explicit error list without raising unhandled exceptions.
"""

import random
import string
import pytest
from models import safe_parse_market_event


def random_string(max_len: int = 30) -> str:
    letters = (
        string.ascii_letters + string.digits + "!@#$%^&*()_+-=[]{}|;':,./<>? \t\n\r"
    )
    return "".join(random.choice(letters) for _ in range(random.randint(0, max_len)))


def random_value():
    choices = [
        random.uniform(-1e12, 1e12),
        random.randint(-(2**65), 2**65),
        float("nan"),
        float("inf"),
        float("-inf"),
        random_string(),
        None,
        True,
        False,
        [random_string() for _ in range(random.randint(0, 5))],
        {"nested": random_string()},
        b"\x00\xff\xfe\xca\xfe\xba\xbe",
    ]
    return random.choice(choices)


def generate_fuzz_payload() -> dict:
    keys = [
        "event_id",
        "instrument_id",
        "symbol",
        "event_type",
        "type",
        "exchange_timestamp",
        "ts",
        "receive_timestamp",
        "source",
        "exchange",
        "sequence_number",
        "seq",
        "price",
        "quantity",
        "size",
        "bid_price",
        "bid",
        "bid_size",
        "bsize",
        "ask_price",
        "ask",
        "ask_size",
        "asize",
        "side",
        "trade_id",
        "bids",
        "asks",
        "is_snapshot",
        random_string(10),
    ]

    payload = {}
    num_fields = random.randint(0, len(keys))
    for _ in range(num_fields):
        k = random.choice(keys)
        payload[k] = random_value()
    return payload


def test_fuzz_parser_never_crashes():
    """Execute 5,000 randomized malformed payloads through the parser.

    Guarantees that 0 exceptions escape the safe_parse_market_event boundary.
    """
    random.seed(12345)  # Deterministic seed (Design Principle 9)

    for i in range(5000):
        # Test 1: Random dictionary payload
        payload = generate_fuzz_payload()
        ev, errors = safe_parse_market_event(payload)
        assert isinstance(errors, list)

        # Test 2: Arbitrary binary / string bytes
        raw_junk = random.choice(
            [
                random_string(100),
                random_string(100).encode("utf-8", errors="replace"),
                b"\x80abc\x00\xff\xfe\xca\xfe\xba\xbe" * 10,
                "",
                "{}",
                "null",
                "12345",
                '{"unclosed": "json',
            ]
        )
        ev, errors = safe_parse_market_event(raw_junk)
        assert isinstance(errors, list)
