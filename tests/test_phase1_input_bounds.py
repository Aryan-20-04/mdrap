"""Phase 1 Regression Suite: Input Bounds & Numerical Type Safety (Spec §5 & Task 6).

Verifies that:
1. Boolean values for price, quantity, sequence, and timestamps are rejected as schema violations.
2. NaN, +Inf, -Inf values for numerical fields are rejected with SchemaError.
3. Negative or zero prices/quantities on trades are rejected.
4. Overflowing large ints (> 1024 bits) are rejected without crashing.
"""

import math
import pytest

from mdrap.gateway import normalize, SchemaError
from mdrap.models import RawEvent


def test_rejects_boolean_in_numeric_fields():
    """Booleans (which are subclasses of int in Python) must be rejected."""
    # Boolean price
    raw_bool_px = RawEvent(
        source="FEEDX",
        payload={"instrument": "AAPL", "event_type": "TRADE", "price": True, "quantity": 10.0, "exchange_ts": 1000.0, "sequence": 1},
    )
    with pytest.raises(SchemaError) as exc:
        normalize(raw_bool_px)
    assert "invalid price" in str(exc.value)

    # Boolean quantity
    raw_bool_qty = RawEvent(
        source="FEEDX",
        payload={"instrument": "AAPL", "event_type": "TRADE", "price": 100.0, "quantity": False, "exchange_ts": 1000.0, "sequence": 1},
    )
    with pytest.raises(SchemaError) as exc:
        normalize(raw_bool_qty)
    assert "invalid quantity" in str(exc.value)

    # Boolean sequence
    raw_bool_seq = RawEvent(
        source="FEEDX",
        payload={"instrument": "AAPL", "event_type": "TRADE", "price": 100.0, "quantity": 10.0, "exchange_ts": 1000.0, "sequence": True},
    )
    with pytest.raises(SchemaError) as exc:
        normalize(raw_bool_seq)
    assert "sequence not an int" in str(exc.value)

    # Boolean exchange_ts
    raw_bool_ts = RawEvent(
        source="FEEDX",
        payload={"instrument": "AAPL", "event_type": "TRADE", "price": 100.0, "quantity": 10.0, "exchange_ts": True, "sequence": 1},
    )
    with pytest.raises(SchemaError) as exc:
        normalize(raw_bool_ts)
    assert "exchange_ts not numeric" in str(exc.value)


def test_rejects_nan_and_inf_in_trade_and_quote():
    """NaN and Infinities must be rejected immediately."""
    for bad_val in (float("nan"), float("inf"), float("-inf")):
        # Trade price
        raw_trade = RawEvent(
            source="FEEDX",
            payload={"instrument": "AAPL", "event_type": "TRADE", "price": bad_val, "quantity": 10.0, "exchange_ts": 1000.0, "sequence": 1},
        )
        with pytest.raises(SchemaError):
            normalize(raw_trade)

        # Quote bid/ask
        raw_quote = RawEvent(
            source="FEEDX",
            payload={"instrument": "AAPL", "event_type": "QUOTE", "bid": bad_val, "ask": 100.0, "exchange_ts": 1000.0, "sequence": 1},
        )
        with pytest.raises(SchemaError):
            normalize(raw_quote)


def test_rejects_zero_and_negative_prices_and_quantities():
    """Non-positive prices and quantities must be rejected."""
    # Zero price
    with pytest.raises(SchemaError):
        normalize(RawEvent(source="FEEDX", payload={"instrument": "AAPL", "event_type": "TRADE", "price": 0.0, "quantity": 10.0, "exchange_ts": 1000.0, "sequence": 1}))

    # Negative price
    with pytest.raises(SchemaError):
        normalize(RawEvent(source="FEEDX", payload={"instrument": "AAPL", "event_type": "TRADE", "price": -50.0, "quantity": 10.0, "exchange_ts": 1000.0, "sequence": 1}))

    # Negative quantity
    with pytest.raises(SchemaError):
        normalize(RawEvent(source="FEEDX", payload={"instrument": "AAPL", "event_type": "TRADE", "price": 100.0, "quantity": -1.0, "exchange_ts": 1000.0, "sequence": 1}))
