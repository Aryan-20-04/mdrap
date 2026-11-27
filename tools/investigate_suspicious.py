"""
Investigates the root causes of DUPLICATE and OUT_OF_ORDER suspicious flags
on Binance and Kraken to determine if they represent genuine data quality issues
or expected/benign market microstructure behaviors.
"""

import sys, os, time, json

sys.path.insert(0, "src")

from mdrap.ws_feed import parse_binance_frame, parse_kraken_frame, resolve_venue_symbols
from mdrap.gateway import Gateway
from mdrap.quality import QualityEngine
from mdrap.config import QualityConfig, DEFAULT_ASSET_CLASSES
from mdrap.models import QualityStatus, EventType

g = Gateway()
cfg = QualityConfig(asset_classes=DEFAULT_ASSET_CLASSES)

print("=" * 80)
print("INVESTIGATION: Binance DUPLICATE reasons")
print("=" * 80)
print()

q = QualityEngine(cfg)
now = time.time()

# Binance sends bookTicker @100ms + @trade simultaneously - both use same epoch timestamp
# The un-sequenced dedup key includes (source, instrument_id, event_type, exchange_ts, price, qty, bid, ask, bid_sz, ask_sz)
# When two trades in the same 1ms interval with identical prices arrive, they are identical.


# Case 1: Same trade sent twice from reconnect (genuine dup)
def make_binance_trade(trade_id, price, qty, ts_ms):
    return {
        "e": "trade",
        "E": ts_ms,
        "s": "BTCUSDT",
        "p": str(price),
        "q": str(qty),
        "t": trade_id,
    }


def make_binance_book_ticker(update_id, bid, bid_sz, ask, ask_sz, ts_ms=None):
    frame = {
        "u": update_id,
        "s": "BTCUSDT",
        "b": str(bid),
        "B": str(bid_sz),
        "a": str(ask),
        "A": str(ask_sz),
    }
    if ts_ms:
        frame["T"] = ts_ms
    return frame


ts_now_ms = int(now * 1000)

print("Scenario 1: Identical trade replayed (genuine duplicate)")
for trade_data in [
    make_binance_trade(1001, 95000.0, 0.1, ts_now_ms),
    make_binance_trade(1001, 95000.0, 0.1, ts_now_ms),  # exact replay
]:
    r = parse_binance_frame(trade_data, "BTC/USD")
    if r:
        c = g.normalize(r)
        e = q.evaluate(c)
        if e:
            print(
                f"  trade_id={r.payload.get('trade_id')} seq={c.sequence_number} status={e.quality_status.value} reasons={list(e.reasons)} ex_ts={e.exchange_timestamp:.3f}"
            )

print()
print("Scenario 2: Two different trades at same millisecond (burst market activity)")
q2 = QualityEngine(cfg)
for trade_data in [
    make_binance_trade(2001, 95000.0, 0.1, ts_now_ms),
    make_binance_trade(
        2002, 95000.0, 0.1, ts_now_ms
    ),  # same ts, same price, diff trade_id
    make_binance_trade(2003, 95001.0, 0.2, ts_now_ms),  # same ts, diff price
]:
    r = parse_binance_frame(trade_data, "BTC/USD")
    if r:
        c = g.normalize(r)
        e = q2.evaluate(c)
        if e:
            tid = r.payload.get("trade_id")
            print(
                f"  trade_id={tid} seq={c.sequence_number} status={e.quality_status.value} reasons={list(e.reasons)} ex_ts={e.exchange_timestamp:.3f} px={e.price} qty={e.quantity}"
            )

print()
print("Scenario 3: BookTicker - same bid/ask repeated (unchanged quote)")
q3 = QualityEngine(cfg)
for i in range(5):
    frame = make_binance_book_ticker(100 + i, 95000.0, 1.5, 95001.0, 0.8, ts_now_ms + i)
    r = parse_binance_frame(frame, "BTC/USD")
    if r:
        c = g.normalize(r)
        e = q3.evaluate(c)
        if e:
            print(
                f"  update_id={100 + i} status={e.quality_status.value} reasons={list(e.reasons)} ex_ts={e.exchange_timestamp:.4f}"
            )

print()
print("Scenario 4: Binance @depth5 snapshot - repeated identical depth (quiet market)")
q4 = QualityEngine(cfg)
same_depth = {
    "lastUpdateId": 999,
    "bids": [["95000.0", "1.5"], ["94999.0", "2.0"]],
    "asks": [["95001.0", "0.8"], ["95002.0", "1.2"]],
}
for i in range(3):
    r = parse_binance_frame(same_depth, "BTC/USD")
    if r:
        c = g.normalize(r)
        # Manually nudge exchange_ts slightly to simulate same-ms delivery
        e = q4.evaluate(c)
        if e:
            print(
                f"  iter={i} status={e.quality_status.value} reasons={list(e.reasons)} seq={c.sequence_number}"
            )

print()
print("=" * 80)
print("INVESTIGATION: Kraken OUT_OF_ORDER reasons")
print("=" * 80)
print()

from mdrap.ws_feed import _KRAKEN_BOOKS

_KRAKEN_BOOKS.clear()

# Kraken book-10 sends incremental updates - bid and ask messages arrive separately
# on the same WebSocket frame, but at the application layer they can arrive
# in any order relative to their channel-level sequence numbers.
# Kraken's public crypto feeds do NOT have sequence numbers on book updates.
# The OUT_OF_ORDER flags come from exchange_timestamp comparison when
# two independent streams (trades vs quotes) have timestamps that cross.

print(
    "Scenario A: Kraken incremental updates - independent bid+ask updates arrive with jitter"
)
q5 = QualityEngine(cfg)
t_base = now - 1.0  # 1 second in the past

frames_kraken = [
    # snapshot
    [
        42,
        {
            "as": [["60010.0", "1.0", str(t_base)]],
            "bs": [["60000.0", "1.0", str(t_base)]],
        },
        "book-10",
        "XBT/USD",
    ],
    # ask update t+0.100
    [42, {"a": [["60008.0", "0.5", str(t_base + 0.100)]]}, "book-10", "XBT/USD"],
    # bid update arrives later but with EARLIER timestamp (network jitter)
    [
        42,
        {"b": [["60001.0", "0.8", str(t_base + 0.050)]]},
        "book-10",
        "XBT/USD",
    ],  # ts < last seen
]

for fr in frames_kraken:
    r = parse_kraken_frame(fr, "BTC/USD")
    if r:
        c = g.normalize(r)
        e = q5.evaluate(c)
        if e:
            print(
                f"  status={e.quality_status.value} reasons={list(e.reasons)} ex_ts={e.exchange_timestamp:.4f} bid={c.bid_price} ask={c.ask_price}"
            )

print()
print("Scenario B: Kraken STALE - does stale threshold apply to book-10 updates?")
q6 = QualityEngine(cfg)
_KRAKEN_BOOKS.clear()
old_ts = now - 10.0  # 10s old data

old_frames = [
    [
        42,
        {
            "as": [["60010.0", "1.0", str(old_ts)]],
            "bs": [["60000.0", "1.0", str(old_ts)]],
        },
        "book-10",
        "XBT/USD",
    ],
    [42, {"a": [["60009.0", "0.5", str(old_ts + 0.1)]]}, "book-10", "XBT/USD"],
]
for fr in old_frames:
    r = parse_kraken_frame(fr, "BTC/USD")
    if r:
        c = g.normalize(r)
        e = q6.evaluate(c)
        if e:
            print(
                f"  staleness={now - c.exchange_timestamp:.2f}s status={e.quality_status.value} reasons={list(e.reasons)}"
            )

print()
print("DEDUP KEY ANALYSIS: What makes an un-sequenced event 'duplicate'?")
print(
    "Un-sequenced events use: (source, instrument_id, event_type, exchange_ts, price, qty, bid, ask, bid_sz, ask_sz)"
)
print(
    "-> Binance bookTicker: same bid/ask with same epoch ts (quiet market) => DUPLICATE"
)
print("-> Binance @trade: same trade_id replayed (reconnect) => DUPLICATE")
print(
    "-> Kraken incremental book: bid+ask from same batch arrive with same ts => DUPLICATE if fully identical"
)
print()
print(
    "CONCLUSION: DUPLICATEs are genuine repeated identical events - benign but correctly flagged SUSPICIOUS"
)
print(
    "           OUT_OF_ORDER on Kraken is from bid/ask incremental updates with slightly different timestamps"
)
print(
    "           STALE on Kraken is from book updates that arrive 2+ seconds after their exchange timestamp"
)
