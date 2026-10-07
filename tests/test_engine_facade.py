"""Phase 7: Developer Experience - Unified mdrap.Engine Facade Tests."""

from __future__ import annotations

import mdrap
from mdrap.models import RawEvent


def test_engine_single_event_process_and_query():
    with mdrap.Engine(db_path=":memory:") as engine:
        raw = RawEvent(
            source="BINANCE",
            payload={
                "instrument": "BTC/USD",
                "event_type": "TRADE",
                "price": 60000.0,
                "quantity": 1.5,
                "sequence": 100,
                "exchange_ts": 1700000000.0,
            },
            receive_timestamp=1700000000.001,
            raw_id="bin_001",
        )
        canon = engine.process(raw)
        assert canon is not None
        assert canon.instrument_id == "BTC/USD"
        assert canon.price == 60000.0

        engine.flush(wait=True)
        rows = engine.query("BTC/USD")
        assert len(rows) >= 1
        assert rows[0]["instrument_id"] == "BTC/USD"

        metrics = engine.metrics()
        assert metrics["processed"] == 1


def test_engine_batch_processing():
    with mdrap.Engine(db_path=":memory:") as engine:
        batch = [
            RawEvent(
                source="COINBASE",
                payload={
                    "instrument": "ETH/USD",
                    "event_type": "TRADE",
                    "price": 3000.0 + i,
                    "quantity": 2.0,
                    "sequence": i + 1,
                    "exchange_ts": 1700000000.0 + i,
                },
                receive_timestamp=1700000000.001 + i,
                raw_id=f"cb_{i}",
            )
            for i in range(5)
        ]
        canons = engine.process_batch(batch)
        assert len(canons) == 5

        engine.flush(wait=True)
        rows = engine.query("ETH/USD")
        assert len(rows) == 5

        summary = engine.metrics()
        assert summary["processed"] == 5
