"""Adversarial reproduction test for P0-C / Section 4.D: C vs Python Stale Seen Capacity Mismatch."""

from mdrap.quality import QualityEngine, QualityConfig, Reason
from mdrap.models import CanonicalEvent, EventType, QualityStatus

def make_canonical(seq: int) -> CanonicalEvent:
    return CanonicalEvent(
        event_id=f"evt_{seq}",
        instrument_id="AAPL",
        event_type=EventType.QUOTE,
        exchange_timestamp=1000.0,
        receive_timestamp=1000.0,
        processing_timestamp=1000.0,
        source="FEED_A",
        sequence_number=seq,
        bid_price=100.0,
        ask_price=100.1,
        bid_size=10.0,
        ask_size=10.0,
        quality_status=QualityStatus.VALID,
    )

def test_stale_seen_capacity_mismatch():
    print("[*] Testing C vs Python stale seen behavior...")
    # 1. Test pure Python QualityEngine
    cfg = QualityConfig(seq_jump_limit=1000)
    qe = QualityEngine(cfg)

    # Establish baseline sequence 200
    qe.evaluate(make_canonical(200))

    # Now send 5 distinct stale sequence numbers (< 200 - 64 = < 136): 50, 51, 52, 53, 54
    for s in [50, 51, 52, 53, 54]:
        qe.evaluate(make_canonical(s))

    # Now resend sequence 50 (should be DUPLICATE)
    ev_dup = make_canonical(50)
    qe.evaluate(ev_dup)
    py_is_dup = Reason.DUPLICATE in ev_dup.reasons
    print(f"[+] Python evaluation for 5th stale sequence resend: is_dup={py_is_dup}, status={ev_dup.quality_status}, reasons={ev_dup.reasons}")


    # Now test Native C fastpath if available
    try:
        from mdrap.fastpath import FastQualityEngine
        fqe = FastQualityEngine(cfg)

        # Submit seq 200
        fqe.evaluate(make_canonical(200))

        # Submit 50, 51, 52, 53, 54
        for s in [50, 51, 52, 53, 54]:
            fqe.evaluate(make_canonical(s))

        # Resend 50
        ev_dup_c = make_canonical(50)
        fqe.evaluate(ev_dup_c)
        c_is_dup = Reason.DUPLICATE in ev_dup_c.reasons
        print(f"[+] Native C evaluation for 5th stale sequence resend: is_dup={c_is_dup}, status={ev_dup_c.quality_status}, reasons={ev_dup_c.reasons}")

        if py_is_dup != c_is_dup:
            print(f"[BUG CONFIRMED] Semantic Parity Divergence: Python detected duplicate={py_is_dup}, Native C detected duplicate={c_is_dup}!")
        else:
            print("[+] Semantic parity holds for this scenario.")
    except Exception as e:
        print(f"[-] Native C engine check encountered: {e}")


if __name__ == "__main__":
    test_stale_seen_capacity_mismatch()
