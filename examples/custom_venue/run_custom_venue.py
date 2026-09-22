"""MDRAP Custom Venue & User Rule Extension Example.

Demonstrates:
1. Implementing the FeedAdapter protocol for a proprietary broker / dark pool.
2. Registering user-defined quality rules in bit range 32..63.
3. Ingesting and evaluating events with full lineage and quality tagging.
"""
import sys
import os

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
src_path = os.path.join(repo_root, "src")
if src_path not in sys.path:
    sys.path.insert(0, src_path)

from models import RawEvent, CanonicalEvent, EventType, QualityStatus
from adapters.template import TemplateCustomVenueAdapter
from rules import register_rule, clear_user_rules
from quality import QualityEngine
from fastpath import FastQualityEngine, HAS_FASTPATH

# Register a custom user rule (bit 32): Flag bid-ask spread > 5.0
@register_rule(
    bit=32,
    name="CUSTOM_WIDE_SPREAD",
    description="Spread between bid and ask exceeds threshold of 5.0",
    severity=QualityStatus.SUSPICIOUS,
)
def check_wide_spread(ev: CanonicalEvent) -> bool:
    if ev.bid_price is not None and ev.ask_price is not None:
        return (ev.ask_price - ev.bid_price) > 5.0
    return False

def main():
    print("=== MDRAP Custom Venue Extension Example ===")
    adapter = TemplateCustomVenueAdapter("DARKPOOL_OMEGA")
    adapter.open()

    engine = FastQualityEngine() if HAS_FASTPATH else QualityEngine()
    print(f"Active Engine: {type(engine).__name__}")

    # Create an event with wide spread (ask 110 - bid 100 = 10 > 5)
    ev = CanonicalEvent(
        event_id="test-1",
        instrument_id="TEST_INST",
        event_type=EventType.QUOTE,
        exchange_timestamp=99.99,
        receive_timestamp=100.0,
        processing_timestamp=0.0,
        source="DARKPOOL_OMEGA",
        sequence_number=1,
        bid_price=100.0,
        ask_price=110.0,
        bid_size=10.0,
        ask_size=10.0,
        reasons=[],
    )

    evaluated = engine.evaluate(ev)
    print(f"Evaluated Status: {evaluated.quality_status.value}")
    print(f"Evaluated Reasons: {evaluated.reasons}")
    assert "CUSTOM_WIDE_SPREAD" in evaluated.reasons
    assert evaluated.quality_status == QualityStatus.SUSPICIOUS
    print("Success: Custom rule CUSTOM_WIDE_SPREAD triggered as expected!")

if __name__ == "__main__":
    main()
