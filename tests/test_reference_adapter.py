"""Phase 13 Feed Adapter Tests.

Tests:
1. ReferenceFeedAdapter implements FeedAdapter protocol
2. connect / disconnect lifecycle
3. packet injection and receive
4. normalization into CanonicalEvent
5. health diagnostics telemetry
"""

from adapters import FeedAdapter
from adapters.reference import ReferenceFeedAdapter
from models import EventType, QualityStatus


def test_reference_feed_adapter_implements_protocol():
    """Verify ReferenceFeedAdapter conforms to runtime_checkable FeedAdapter protocol."""
    adapter = ReferenceFeedAdapter(source_name="TEST_FEED", symbol="AAPL")
    assert isinstance(adapter, FeedAdapter)


def test_reference_feed_adapter_lifecycle_and_streaming():
    """Test full cycle: connect, receive packets, normalize, and disconnect."""
    adapter = ReferenceFeedAdapter(source_name="EXCHANGE_A", symbol="MSFT")

    # Connecting
    adapter.connect()
    h = adapter.health()
    assert h["connected"] is True
    assert h["status"] == "HEALTHY"

    # Injecting simulated packets
    adapter.feed_simulated_packet(seq=1, price=400.50, qty=50.0, side="BUY")
    adapter.feed_simulated_packet(seq=2, price=400.55, qty=25.0, side="SELL")

    # Receiving
    raw1 = adapter.receive()
    assert raw1 is not None
    assert raw1.source == "EXCHANGE_A"

    raw2 = adapter.receive()
    assert raw2 is not None

    # Empty buffer returns None
    assert adapter.receive() is None

    # Normalization
    ev1 = adapter.normalize(raw1)
    assert ev1.instrument_id == "MSFT"
    assert ev1.event_type == EventType.TRADE
    assert ev1.price == 400.50
    assert ev1.quantity == 50.0
    assert ev1.quality_status == QualityStatus.VALID

    # Disconnecting
    adapter.disconnect()
    h_disc = adapter.health()
    assert h_disc["connected"] is False
