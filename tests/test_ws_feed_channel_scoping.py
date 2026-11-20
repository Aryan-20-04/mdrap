"""
Test verifying multi-channel SequenceScope isolation in WebSocket feeds (VULN-05).
"""

from mdrap.ws_feed import SequenceScope, LiveCryptoFeed


def test_sequence_scope_channel_isolation():
    """Verify that multiple channels for the same symbol do not collide on sequence numbers."""
    feed = LiveCryptoFeed()

    scope_trades = SequenceScope(venue="BINANCE", instrument="BTCUSDT", channel="TRADE", epoch=1)
    scope_depth = SequenceScope(venue="BINANCE", instrument="BTCUSDT", channel="DEPTH", epoch=1)

    assert scope_trades.key() != scope_depth.key()
    assert "TRADE" in scope_trades.key()
    assert "DEPTH" in scope_depth.key()

    # Interleaved sequences on distinct channels
    # Trade sequence starts at 100
    gap1 = feed.check_sequence_gap(scope_trades, 100)
    assert not gap1

    # Depth sequence starts at 5000 (different range)
    gap2 = feed.check_sequence_gap(scope_depth, 5000)
    assert not gap2

    # Contiguous trade update (101)
    gap3 = feed.check_sequence_gap(scope_trades, 101)
    assert not gap3

    # Contiguous depth update (5001)
    gap4 = feed.check_sequence_gap(scope_depth, 5001)
    assert not gap4

    # Simulated gap on trade stream (105 instead of 102)
    gap5 = feed.check_sequence_gap(scope_trades, 105)
    assert gap5, "Expected sequence gap on trade stream"
    assert feed.gap_counts[scope_trades.key()] == 1
    assert feed.missing_events_count[scope_trades.key()] == 3

    # Depth stream remains unaffected
    gap6 = feed.check_sequence_gap(scope_depth, 5002)
    assert not gap6, "Depth stream should not report a gap when trade stream had a gap"


def test_stale_and_duplicate_sequences_preserve_high_watermark():
    feed = LiveCryptoFeed()
    scope = SequenceScope("BINANCE", "BTCUSDT", "TRADE", 1)
    assert not feed.check_sequence_gap(scope, 100)
    assert not feed.check_sequence_gap(scope, 50)  # stale/out of order
    assert feed.last_seq[scope.key()] == 100
    assert not feed.check_sequence_gap(scope, 100)  # duplicate
    assert feed.duplicate_count[scope.key()] == 1
    assert not feed.check_sequence_gap(scope, 101)
    assert feed.last_seq[scope.key()] == 101
    assert feed.missing_events_count[scope.key()] == 0


def test_explicit_reset_snapshot_and_epoch_start_new_sequence_scope():
    feed = LiveCryptoFeed()
    scope = SequenceScope("BINANCE", "BTCUSDT", "DEPTH", 1)
    assert not feed.check_sequence_gap(scope, 100)
    assert not feed.check_sequence_gap(scope, 2, is_reset=True)
    assert feed.last_seq[scope.key()] == 2
    assert not feed.check_sequence_gap(scope, 3)
    assert not feed.check_sequence_gap(scope, 10, is_snapshot=True)
    assert feed.last_seq[scope.key()] == 10

    next_epoch = SequenceScope("BINANCE", "BTCUSDT", "DEPTH", 2)
    assert not feed.check_sequence_gap(next_epoch, 1)
    assert feed.last_seq[next_epoch.key()] == 1
    assert feed.last_seq[scope.key()] == 10


def test_stale_sequence_isolated_by_instrument_and_channel():
    feed = LiveCryptoFeed()
    trade = SequenceScope("BINANCE", "BTCUSDT", "TRADE", 3)
    other_instrument = SequenceScope("BINANCE", "ETHUSDT", "TRADE", 3)
    depth = SequenceScope("BINANCE", "BTCUSDT", "DEPTH", 3)
    assert not feed.check_sequence_gap(trade, 100)
    assert not feed.check_sequence_gap(other_instrument, 50)
    assert not feed.check_sequence_gap(depth, 9)
    assert not feed.check_sequence_gap(trade, 50)
    assert not feed.check_sequence_gap(trade, 101)
    assert feed.last_seq[trade.key()] == 101
    assert feed.last_seq[other_instrument.key()] == 50
    assert feed.last_seq[depth.key()] == 9
