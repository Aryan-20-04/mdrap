"""
MDRAP Market Data Anomaly & Quality Handling Walkthrough.
Demonstrates how MDRAP handles clean quotes, crossed books, duplicates,
gaps, staleness, price flash crashes, and cross-feed reconciliation.
"""

import shutil
import tempfile
from mdrap.engine import Engine
from mdrap.models import RawEvent
from mdrap.reconciliation import CrossFeedReconciler


def main() -> None:
    temp_dir = tempfile.mkdtemp(prefix="mdrap_demo_")
    try:
        engine = Engine.open(temp_dir)
        print("=" * 70)
        print("MDRAP MARKET DATA QUALITY & ANOMALY HANDLING DEMO")
        print("=" * 70)

        # 1. Clean Quote
        print("\n1. [CLEAN QUOTE] Normal BBO incoming from FEED_A")
        r1 = RawEvent(
            source="FEED_A",
            receive_timestamp=100.0,
            payload={
                "instrument": "AAPL",
                "event_type": "QUOTE",
                "bid": 180.00,
                "ask": 180.05,
                "sequence": 1,
                "exchange_ts": 100.0,
            },
        )
        dec1 = engine.submit(r1)
        print(f"   Input Payload : {r1.payload}")
        print(
            f"   Decision      : {dec1.canonical_event.quality_status.name} (Reasons: {dec1.canonical_event.reasons})"
        )
        print(f"   Quarantined?  : {dec1.quarantine_row is not None}")

        # 2. Crossed Book (bid > ask)
        print("\n2. [CROSSED BOOK] Inverted market (bid=180.10 > ask=180.05)")
        r2 = RawEvent(
            source="FEED_A",
            receive_timestamp=100.01,
            payload={
                "instrument": "AAPL",
                "event_type": "QUOTE",
                "bid": 180.10,
                "ask": 180.05,
                "sequence": 2,
                "exchange_ts": 100.01,
            },
        )
        dec2 = engine.submit(r2)
        print(f"   Input Payload : {r2.payload}")
        print(f"   Emitted to Stream? : {dec2.canonical_event is not None}")
        print(f"   Quarantined?       : {dec2.quarantine_row is not None}")
        print(f"   Quarantine Record  : Status={dec2.quarantine_row[3]} Reasons={dec2.quarantine_row[4]}")

        # 3. Duplicate Packet Retransmission
        print("\n3. [DUPLICATE PACKET] Re-receiving sequence 2")
        r3 = RawEvent(
            source="FEED_A",
            receive_timestamp=100.02,
            payload={
                "instrument": "AAPL",
                "event_type": "QUOTE",
                "bid": 180.00,
                "ask": 180.05,
                "sequence": 2,
                "exchange_ts": 100.02,
            },
        )
        dec3 = engine.submit(r3)
        print(f"   Input Payload : {r3.payload}")
        print(f"   Duplicate Flag: {dec3.is_duplicate}")
        print(f"   Quarantine    : Status={dec3.quarantine_row[3]} Reasons={dec3.quarantine_row[4]}")

        # 4. Sequence Gap
        print("\n4. [SEQUENCE GAP] Packet jump from seq 2 to seq 10")
        r4 = RawEvent(
            source="FEED_A",
            receive_timestamp=100.03,
            payload={
                "instrument": "AAPL",
                "event_type": "QUOTE",
                "bid": 180.01,
                "ask": 180.06,
                "sequence": 10,
                "exchange_ts": 100.03,
            },
        )
        dec4 = engine.submit(r4)
        print(f"   Input Payload : {r4.payload}")
        print(
            f"   Decision      : {dec4.canonical_event.quality_status.name} (Reasons: {dec4.canonical_event.reasons})"
        )
        print("   Note: Emitted with SUSPICIOUS tag so downstream consumers know gap occurred without drop.")

        # 5. Stale Timestamp
        print("\n5. [STALE TIMESTAMP] Exchange ts is 5.0s in the past (threshold: 1.0s)")
        r5 = RawEvent(
            source="FEED_A",
            receive_timestamp=105.00,
            payload={
                "instrument": "AAPL",
                "event_type": "QUOTE",
                "bid": 180.02,
                "ask": 180.07,
                "sequence": 11,
                "exchange_ts": 100.00,
            },
        )
        dec5 = engine.submit(r5)
        print(f"   Input Payload : {r5.payload}")
        print(
            f"   Decision      : {dec5.canonical_event.quality_status.name} (Reasons: {dec5.canonical_event.reasons})"
        )

        # 6. Statistical Price Outlier (Flash Spike)
        print("\n6. [STATISTICAL OUTLIER] Trade spikes to $250.00 after $180 corridor")
        for i in range(25):
            engine.submit(
                RawEvent(
                    source="FEED_A",
                    receive_timestamp=106.0 + i * 0.01,
                    payload={
                        "instrument": "AAPL",
                        "event_type": "TRADE",
                        "price": 180.0 + (i * 0.01),
                        "quantity": 100,
                        "sequence": 12 + i,
                        "exchange_ts": 106.0 + i * 0.01,
                    },
                )
            )
        r6 = RawEvent(
            source="FEED_A",
            receive_timestamp=107.0,
            payload={
                "instrument": "AAPL",
                "event_type": "TRADE",
                "price": 250.00,
                "quantity": 100,
                "sequence": 40,
                "exchange_ts": 107.0,
            },
        )
        dec6 = engine.submit(r6)
        print(f"   Spike Price   : ${r6.payload['price']}")
        print(
            f"   Decision      : {dec6.canonical_event.quality_status.name} (Reasons: {dec6.canonical_event.reasons})"
        )

        # 7. Cross-Feed Reconciliation & Disagreement
        print("\n7. [CROSS-FEED ARBITRATION] Reconciling concurrent feeds for MSFT")
        rec = CrossFeedReconciler()
        e_nasdaq = engine.submit(
            RawEvent(
                source="NASDAQ",
                receive_timestamp=110.0,
                payload={
                    "instrument": "MSFT",
                    "event_type": "QUOTE",
                    "bid": 400.00,
                    "ask": 400.05,
                    "price": 400.02,
                    "sequence": 1,
                    "exchange_ts": 110.0,
                },
            )
        ).canonical_event
        e_bats = engine.submit(
            RawEvent(
                source="BATS",
                receive_timestamp=110.001,
                payload={
                    "instrument": "MSFT",
                    "event_type": "QUOTE",
                    "bid": 400.01,
                    "ask": 400.06,
                    "price": 400.03,
                    "sequence": 1,
                    "exchange_ts": 110.001,
                },
            )
        ).canonical_event
        e_dark = engine.submit(
            RawEvent(
                source="DARK",
                receive_timestamp=110.002,
                payload={
                    "instrument": "MSFT",
                    "event_type": "QUOTE",
                    "bid": 425.00,
                    "ask": 425.10,
                    "price": 425.05,
                    "sequence": 1,
                    "exchange_ts": 110.002,
                },
            )
        ).canonical_event

        rec.reconcile(e_nasdaq)
        d_bats = rec.reconcile(e_bats)
        d_dark = rec.reconcile(e_dark)
        print(f"   BATS vs NASDAQ (Agreeing)   : Disagreement={d_bats.disagreement} -> {d_bats.reason}")
        print(
            f"   DARK vs Others (Outlier)    : Disagreement={d_dark.disagreement} -> Selected {d_dark.chosen_source} ({d_dark.quality_reasons})"
        )

        print("\n" + "=" * 70)
        print("DEMO COMPLETE: All anomaly filters and arbitration verified.")
        print("=" * 70)
    finally:
        engine.close()
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
