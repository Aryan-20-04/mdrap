import pytest
from models import CanonicalEvent, EventType, QualityStatus
from rules import register_rule, unregister_rule, clear_user_rules, evaluate_user_rules
from adapters import FeedAdapter, discover_adapters
from adapters.template import TemplateCustomVenueAdapter
from quality import QualityEngine
from fastpath import FastQualityEngine, HAS_FASTPATH

@pytest.fixture(autouse=True)
def clean_rules():
    clear_user_rules()
    yield
    clear_user_rules()

def test_feed_adapter_protocol():
    adapter = TemplateCustomVenueAdapter("TEST_VENUE")
    assert isinstance(adapter, FeedAdapter)
    adapter.open()
    events = list(adapter)
    assert len(events) == 100
    assert events[0].source == "TEST_VENUE"
    adapter.close()
    with pytest.raises(RuntimeError):
        list(adapter)

def test_user_rule_registration_bounds():
    # Bits < 32 are reserved for core platform rules
    with pytest.raises(ValueError, match="out of range"):
        @register_rule(bit=15, name="ILLEGAL_CORE_OVERWRITE")
        def r1(ev): return False

    with pytest.raises(ValueError, match="out of range"):
        @register_rule(bit=64, name="OVERFLOW_BIT")
        def r2(ev): return False

    # Valid registration in 32..63
    @register_rule(bit=32, name="CUSTOM_VALID_1")
    def r3(ev): return False

    # Duplicate bit rejection
    with pytest.raises(ValueError, match="already registered"):
        @register_rule(bit=32, name="CUSTOM_VALID_2")
        def r4(ev): return False

def test_user_rule_execution_and_escalation():
    @register_rule(bit=40, name="ANOMALOUS_SIZE", severity=QualityStatus.INVALID)
    def check_size(ev):
        return ev.quantity is not None and ev.quantity > 1000.0

    ev = CanonicalEvent(
        event_id="test-1",
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=100.0,
        receive_timestamp=100.001,
        processing_timestamp=0.0,
        source="FEEDX",
        sequence_number=1,
        price=150.0,
        quantity=5000.0,
        quality_status=QualityStatus.VALID,
        reasons=[],
    )

    engine = FastQualityEngine() if HAS_FASTPATH else QualityEngine()
    evaluated = engine.evaluate(ev)

    assert evaluated.quality_status == QualityStatus.INVALID
    assert "ANOMALOUS_SIZE" in evaluated.reasons
