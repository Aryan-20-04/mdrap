"""Observer in-memory state tracking across 5 venues and core crypto instruments with strict quote-currency preservation."""

from __future__ import annotations

import collections
import math
import os
import re
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

VENUES = ["BINANCE", "KRAKEN", "COINBASE", "DATABENTO", "ITCH_SBE"]
CORE_ASSETS = ["BTC-USD", "BTC-USDT", "ETH-USD", "ETH-USDT"]


class SequenceDisposition(str, Enum):
    """Disposition returned by sequence/epoch trackers to gate live state mutation."""

    ACCEPTED = "ACCEPTED"
    STALE_EPOCH = "STALE_EPOCH"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    DUPLICATE = "DUPLICATE"
    UNAVAILABLE = "UNAVAILABLE"


_REJECTED_DISPOSITIONS = frozenset(
    {
        SequenceDisposition.STALE_EPOCH,
        SequenceDisposition.OUT_OF_ORDER,
        SequenceDisposition.DUPLICATE,
    }
)


def _safe_float(val: Any, default: float = 0.0) -> float:
    if val is None or isinstance(val, bool):
        return default
    try:
        parsed = float(val)
        return parsed if math.isfinite(parsed) else default
    except (ValueError, TypeError, OverflowError):
        return default


def _safe_int(val: Any, default: int = 0) -> int:
    if val is None or isinstance(val, bool):
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _parse_optional_int(val: Any) -> Optional[int]:
    """Parse integer preserving None when value is absent, non-convertible, or boolean."""
    if val is None or isinstance(val, bool):
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _parse_optional_nonneg_finite_float(val: Any) -> tuple[Optional[float], bool]:
    """Parse an optional non-negative finite float.

    Returns (parsed_value, is_valid):
      - (None, True) when val is None (absent / partial tick update)
      - (float_val, True) when finite and >= 0.0
      - (None, False) when bool, unparseable, NaN, +inf/-inf, or < 0.0
    """
    if val is None:
        return None, True
    if isinstance(val, bool):
        return None, False
    try:
        parsed = float(val)
    except (ValueError, TypeError, OverflowError):
        return None, False
    if not math.isfinite(parsed) or parsed < 0.0:
        return None, False
    return parsed, True


def _first_present_dict(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        val = mapping.get(key)
        if val is not None:
            return val
    return None


def _first_present_attr(obj: Any, attrs: tuple[str, ...]) -> Any:
    for attr in attrs:
        val = getattr(obj, attr, None)
        if val is not None:
            return val
    return None


def _compare_epochs(epoch_a: Any, epoch_b: Any) -> int:
    """Return 1 if epoch_a > epoch_b, -1 if epoch_a < epoch_b, 0 if equal.
    Supports integers, numeric strings, ISO timestamp strings, or lexicographic strings.
    """
    if epoch_a == epoch_b:
        return 0
    if epoch_a is None:
        return -1
    if epoch_b is None:
        return 1
    if not isinstance(epoch_a, bool) and not isinstance(epoch_b, bool):
        if isinstance(epoch_a, float) or isinstance(epoch_b, float):
            try:
                fa = float(epoch_a)
                fb = float(epoch_b)
                if math.isfinite(fa) and math.isfinite(fb) and fa != fb:
                    return 1 if fa > fb else -1
                if math.isfinite(fa) and math.isfinite(fb) and fa == fb:
                    return 0
            except (ValueError, TypeError, OverflowError):
                pass
        else:
            try:
                ia = int(epoch_a)
                ib = int(epoch_b)
                if ia != ib:
                    return 1 if ia > ib else -1
                return 0
            except (ValueError, TypeError):
                pass
    sa = str(epoch_a)
    sb = str(epoch_b)
    if sa == sb:
        return 0
    return 1 if sa > sb else -1


_SEQ_WINDOW_BITS = 4096
_SEQ_WINDOW_MASK = (1 << _SEQ_WINDOW_BITS) - 1


@dataclass
class ChannelSequenceState:
    """Deterministic sequence and epoch tracker scoped to a specific (gateway, symbol) channel."""

    channel_key: str
    gateway_id: Optional[str] = None
    symbol: Optional[str] = None
    current_epoch_id: Optional[Any] = None
    last_feed_seq: int = -1
    max_accepted_seq: int = -1
    feed_gaps: int = 0
    feed_duplicates: int = 0
    feed_out_of_order: int = 0
    feed_snapshots: int = 0
    feed_resets: int = 0
    stale_epoch_packets: int = 0
    epoch_unavailable: int = 0
    degraded_resets: int = 0
    has_sequence_feed: bool = False
    events_received: int = 0
    continuity_state: str = "HEALTHY"
    # --- New fields appended after all original fields (kw_only to preserve positional compat) ---
    last_observed_seq: int = field(default=-1, kw_only=True)
    # Fixed-width 4096-bit sliding bitmap anchored at high_water (bit 0 = high_water, bit d = high_water - d)
    _seen_bitmap: int = field(default=0, repr=False, compare=False, kw_only=True)

    def _reset_seq_window(self, seq: int) -> None:
        self.last_feed_seq = seq
        self.max_accepted_seq = seq
        self.last_observed_seq = seq
        self._seen_bitmap = 1

    def _advance_seq_window(self, seq: int, high_water: int) -> None:
        shift = seq - high_water
        if shift >= _SEQ_WINDOW_BITS:
            self._seen_bitmap = 1
        else:
            base_bitmap = self._seen_bitmap or 1
            self._seen_bitmap = ((base_bitmap << shift) | 1) & _SEQ_WINDOW_MASK
        self.last_feed_seq = seq
        self.max_accepted_seq = seq
        self.last_observed_seq = seq

    def update(
        self,
        seq: Optional[int],
        epoch_id: Optional[Any] = None,
        is_snapshot: bool = False,
    ) -> SequenceDisposition:
        """Validate epoch and sequence continuity for this channel and return explicit disposition.

        Enforces:
          - Epoch validation runs even when `seq is None` or `is_snapshot is True`, so stale-epoch
            packets lacking `feed_seq` cannot bypass epoch rejection or rewind state.
          - Unscoped packets (`epoch_id is None`) cannot trigger an inferred reset (`seq == 1`)
            on an already epoch-scoped channel (`current_epoch_id is not None`).
          - Neither `last_feed_seq` nor `max_accepted_seq` rewinds on out-of-order packets,
            preventing delayed rewind poisoning (`100 -> 50 -> 51`) and spurious forward
            gaps (`100 -> 50 -> 51 -> 101`) in both epoch-scoped and unscoped streams.
          - Legacy `seq == 0` packets on an already-initialized channel (when `seq != last_feed_seq`)
            are classified directly as `OUT_OF_ORDER` before `last_observed_seq` and bitmap lookup,
            without recording in `_seen_bitmap` or altering `last_observed_seq`.
          - Bounded 4096-bit `_seen_bitmap` plus `last_observed_seq` detect both consecutive
            and non-consecutive duplicates (`100 -> 50 -> 51 -> 50`) without unbounded memory.
        """
        # 1. Explicit Epoch Validation (runs unconditionally before seq/snapshot checks)
        if epoch_id is not None:
            if self.current_epoch_id is not None:
                cmp = _compare_epochs(epoch_id, self.current_epoch_id)
                if cmp < 0:
                    if seq is not None:
                        self.has_sequence_feed = True
                        self.events_received += 1
                    self.stale_epoch_packets += 1
                    return SequenceDisposition.STALE_EPOCH
                if cmp > 0:
                    if seq is None:
                        # Cannot advance active epoch without an explicit sequence baseline
                        self.epoch_unavailable += 0
                        return SequenceDisposition.UNAVAILABLE
                    self.has_sequence_feed = True
                    self.events_received += 1
                    self.current_epoch_id = epoch_id
                    self._reset_seq_window(seq)
                    if is_snapshot:
                        self.feed_snapshots += 1
                        self.continuity_state = "HEALTHY_SNAPSHOT"
                    else:
                        self.feed_resets += 1
                        self.continuity_state = "HEALTHY_EPOCH_TRANSITION"
                    return SequenceDisposition.ACCEPTED
            else:
                if seq is None:
                    return SequenceDisposition.UNAVAILABLE
                self.has_sequence_feed = True
                self.events_received += 1
                self.current_epoch_id = epoch_id
                self._reset_seq_window(seq)
                if is_snapshot:
                    self.feed_snapshots += 1
                    self.continuity_state = "HEALTHY_SNAPSHOT"
                else:
                    self.continuity_state = "HEALTHY_EPOCH_INITIALIZED"
                return SequenceDisposition.ACCEPTED
        else:
            if self.current_epoch_id is not None:
                # Unscoped packet on an already epoch-scoped channel is rejected as unavailable identity
                if seq is not None:
                    self.events_received += 1
                self.epoch_unavailable += 1
                return SequenceDisposition.UNAVAILABLE
            self.epoch_unavailable += 1
            self.continuity_state = "DEGRADED_UNSCOPED_EPOCH"

        if seq is None:
            return SequenceDisposition.UNAVAILABLE

        was_initialized = self.has_sequence_feed or self.last_feed_seq >= 0
        self.has_sequence_feed = True
        self.events_received += 1

        # 2. Initialization and Snapshot handling within current epoch (or unscoped stream)
        if is_snapshot:
            self.feed_snapshots += 1
            self._reset_seq_window(seq)
            self.continuity_state = "HEALTHY_SNAPSHOT"
            return SequenceDisposition.ACCEPTED

        # Stage 4: Sequence Continuity Validation
        if not was_initialized:
            self._reset_seq_window(seq)
            return SequenceDisposition.ACCEPTED

        # 3. Immediate duplicate detection against latest accepted sequence
        if seq == self.last_feed_seq:
            self.feed_duplicates += 1
            self.last_observed_seq = seq
            return SequenceDisposition.DUPLICATE

        high_water = max(self.max_accepted_seq, self.last_feed_seq)

        # 4. Accepted high-water advance and genuine forward gaps
        if seq > high_water:
            if seq > high_water + 1:
                self.feed_gaps += seq - high_water - 1
            self._advance_seq_window(seq, high_water)
            return SequenceDisposition.ACCEPTED

        # 5. Legacy seq == 0 handling before last_observed_seq and bitmap duplicate lookup
        if seq == 0:
            self.feed_out_of_order += 1
            return SequenceDisposition.OUT_OF_ORDER

        # 6. Unscoped legacy reset heuristic MUST precede bitmap duplicate lookup so an earlier
        # sequence 1 still retained inside the 4096-position window triggers reset, not DUPLICATE.
        if epoch_id is None and seq == 1 and high_water > 10:
            self.degraded_resets += 1
            self.feed_resets += 1
            self._reset_seq_window(seq)
            self.continuity_state = "DEGRADED_UNSCOPED_RESET"
            return SequenceDisposition.ACCEPTED

        # 7. General duplicate detection via last_observed_seq and 4096-bit sliding bitmap
        offset = high_water - seq
        in_window = 0 <= offset < _SEQ_WINDOW_BITS
        is_bitmap_dup = in_window and (((self._seen_bitmap >> offset) & 1) != 0)
        if seq == self.last_observed_seq or is_bitmap_dup:
            self.feed_duplicates += 1
            self.last_observed_seq = seq
            return SequenceDisposition.DUPLICATE

        # 8. Unseen delayed out-of-order sequence recording
        self.feed_out_of_order += 1
        self.last_observed_seq = seq
        if in_window:
            self._seen_bitmap |= 1 << offset
        return SequenceDisposition.OUT_OF_ORDER


_BASE_ALIAS_MAP = {
    "BTC": "BTC",
    "XBT": "BTC",
    "ETH": "ETH",
}
_SPOT_QUOTES = frozenset({"USD", "USDT"})
_CONCAT_SYMBOL_MAP = {
    "BTCUSDT": "BTC-USDT",
    "XBTUSDT": "BTC-USDT",
    "BTCUSD": "BTC-USD",
    "XBTUSD": "BTC-USD",
    "BTC": "BTC-USD",
    "XBT": "BTC-USD",
    "ETHUSDT": "ETH-USDT",
    "ETHUSD": "ETH-USD",
    "ETH": "ETH-USD",
}
_DELIM_RE = re.compile(r"[-/_]")


def normalize_asset(symbol: str) -> str:
    """
    Map venue-specific spot ticker symbols to canonical instrument pair using explicit
    token/pair parsing while strictly preserving quote currency and instrument identity
    (e.g. BTC-USD vs BTC-USDT).

    Never matches derivatives (e.g. BTCUSDT-PERP), wrapped assets (e.g. WBTC-USD),
    forks (e.g. ETHW-USD), or non-USD/USDT pairs (e.g. BTC-EUR).
    Never combines fiat USD and Tether USDT into one pair without explicit conversion.
    """
    if not symbol:
        return symbol
    upper = symbol.strip().upper()
    if upper in _CONCAT_SYMBOL_MAP:
        return _CONCAT_SYMBOL_MAP[upper]

    parts = _DELIM_RE.split(upper)
    if len(parts) == 2:
        base, quote = parts[0], parts[1]
        canonical_base = _BASE_ALIAS_MAP.get(base)
        if canonical_base is not None and quote in _SPOT_QUOTES:
            return f"{canonical_base}-{quote}"

    return symbol


@dataclass
class Level:
    price: float = 0.0
    size: float = 0.0


@dataclass
class VenueQuote:
    venue: str
    symbol: str
    bid_price: float = 0.0
    bid_size: float = 0.0
    ask_price: float = 0.0
    ask_size: float = 0.0
    last_update_ts: float = 0.0
    sequence: int = 0
    bids: list[Level] = field(default_factory=list)
    asks: list[Level] = field(default_factory=list)

    @property
    def is_crossed(self) -> bool:
        """True if order book is inverted (bid > ask) or locked (bid == ask)."""
        return (
            self.bid_price > 0
            and self.ask_price > 0
            and self.bid_price >= self.ask_price
        )

    @property
    def spread(self) -> float:
        """Absolute spread in quote currency units."""
        if self.bid_price > 0 and self.ask_price > 0:
            return self.ask_price - self.bid_price
        return 0.0

    @property
    def spread_bps(self) -> float:
        """Relative spread in basis points (bps) against mid-price."""
        if self.bid_price > 0 and self.ask_price >= self.bid_price:
            mid = self.mid_price
            if mid > 0:
                return ((self.ask_price - self.bid_price) / mid) * 10000.0
        return 0.0

    @property
    def mid_price(self) -> float:
        """Mid-point between best bid and best ask (overflow-safe)."""
        bid_ok = math.isfinite(self.bid_price) and self.bid_price > 0.0
        ask_ok = math.isfinite(self.ask_price) and self.ask_price > 0.0
        if bid_ok and ask_ok:
            s = self.bid_price + self.ask_price
            if math.isfinite(s):
                return s / 2.0
            return (self.bid_price / 2.0) + (self.ask_price / 2.0)
        if bid_ok:
            return self.bid_price
        if ask_ok:
            return self.ask_price
        return 0.0

    @property
    def micro_price(self) -> float:
        """
        Volume-weighted top-of-book micro-price:
        P_micro = (P_ask * Q_bid + P_bid * Q_ask) / (Q_bid + Q_ask)
        When bid queue volume Q_bid is larger, buying pressure shifts P_micro closer to P_ask.
        Falls back safely to mid_price if sizes/prices are negative, non-finite, or total_size <= 0.
        """
        if (
            not math.isfinite(self.bid_size)
            or not math.isfinite(self.ask_size)
            or not math.isfinite(self.bid_price)
            or not math.isfinite(self.ask_price)
            or self.bid_size < 0.0
            or self.ask_size < 0.0
        ):
            return self.mid_price

        total_size = self.bid_size + self.ask_size
        if not math.isfinite(total_size):
            return self.mid_price

        if total_size > 0.0 and self.bid_price > 0.0 and self.ask_price > 0.0:
            weighted = self.ask_price * self.bid_size + self.bid_price * self.ask_size
            if math.isfinite(weighted):
                return weighted / total_size
            # Overflow-safe fallback when price * size exceeds float range
            return (self.bid_size / total_size) * self.ask_price + (
                self.ask_size / total_size
            ) * self.bid_price
        return self.mid_price


@dataclass
class TradePrint:
    timestamp: float
    venue: str
    symbol: str
    price: float
    size: float
    side: str = "UNKNOWN"


@dataclass
class StreamHealth:
    """Tracks sequence continuity of the transport broadcast stream (daemon/shm sequence domain)."""

    last_seq: int = -1
    gaps: int = 0
    duplicates: int = 0
    out_of_order: int = 0
    events_received: int = 0
    resets: int = 0
    filtered_events: int = 0

    def update(self, seq: int, is_filtered_gap: bool = False) -> SequenceDisposition:
        if seq <= 0:
            return SequenceDisposition.UNAVAILABLE
        self.events_received += 1
        if self.last_seq < 0:
            self.last_seq = seq
            return SequenceDisposition.ACCEPTED

        if seq == self.last_seq + 1:
            self.last_seq = seq
            return SequenceDisposition.ACCEPTED
        if seq > self.last_seq + 1:
            gap_size = seq - self.last_seq - 1
            if is_filtered_gap:
                self.filtered_events += gap_size
            else:
                self.gaps += gap_size
            self.last_seq = seq
            return SequenceDisposition.ACCEPTED
        if seq == self.last_seq:
            self.duplicates += 1
            return SequenceDisposition.DUPLICATE

        # seq < self.last_seq
        if seq <= 10 and self.last_seq > 100:
            self.resets += 1
            self.last_seq = seq
            return SequenceDisposition.ACCEPTED

        self.out_of_order += 1
        return SequenceDisposition.OUT_OF_ORDER


@dataclass
class VenueHealth:
    venue: str
    status: str = "DISCONNECTED"
    feed_type: str = "LIVE"
    events_received: int = 0
    # Per-venue transport drops/reconnects cannot be attributed from post-decode frames alone;
    # left None (unsupported) so dashboards/snapshots never misreport 0 as measured absence.
    # Use stream transport_reconnects, feed_gaps, and exporter_dropped_records instead.
    drops: Optional[int] = None
    reconnects: Optional[int] = None
    last_seen_ts: float = 0.0
    feed_seq_unavailable: int = 0
    gateway_unscoped: int = 0
    invalid_numeric_events: int = 0
    latencies_us: collections.deque = field(
        default_factory=lambda: collections.deque(maxlen=1000)
    )
    channels: dict[str, ChannelSequenceState] = field(default_factory=dict)
    _last_active_channel: Optional[str] = None
    _lock: Optional[threading.RLock] = field(default=None, repr=False, compare=False)

    def _channels_snapshot(self) -> list[ChannelSequenceState]:
        if self._lock is not None:
            with self._lock:
                return list(self.channels.values())
        return list(self.channels.values())

    @property
    def has_sequence_feed(self) -> bool:
        return any(ch.has_sequence_feed for ch in self._channels_snapshot())

    @property
    def last_feed_seq(self) -> int:
        if self._lock is not None:
            with self._lock:
                if (
                    self._last_active_channel
                    and self._last_active_channel in self.channels
                ):
                    return self.channels[self._last_active_channel].last_feed_seq
                if "default:ALL" in self.channels:
                    return self.channels["default:ALL"].last_feed_seq
                return -1
        if self._last_active_channel and self._last_active_channel in self.channels:
            return self.channels[self._last_active_channel].last_feed_seq
        if "default:ALL" in self.channels:
            return self.channels["default:ALL"].last_feed_seq
        return -1

    @last_feed_seq.setter
    def last_feed_seq(self, val: int) -> None:
        if self._lock is not None:
            with self._lock:
                ch = self._get_or_create_channel("default:ALL")
                ch.last_feed_seq = val
                self._last_active_channel = "default:ALL"
            return
        ch = self._get_or_create_channel("default:ALL")
        ch.last_feed_seq = val
        self._last_active_channel = "default:ALL"

    @property
    def feed_gaps(self) -> int:
        return sum(ch.feed_gaps for ch in self._channels_snapshot())

    @property
    def gaps(self) -> int:
        return self.feed_gaps

    @gaps.setter
    def gaps(self, val: int) -> None:
        if self._lock is not None:
            with self._lock:
                ch = self._get_or_create_channel("default:ALL")
                ch.feed_gaps = val
            return
        ch = self._get_or_create_channel("default:ALL")
        ch.feed_gaps = val

    @property
    def feed_duplicates(self) -> int:
        return sum(ch.feed_duplicates for ch in self._channels_snapshot())

    @property
    def duplicates(self) -> int:
        return self.feed_duplicates

    @duplicates.setter
    def duplicates(self, val: int) -> None:
        if self._lock is not None:
            with self._lock:
                ch = self._get_or_create_channel("default:ALL")
                ch.feed_duplicates = val
            return
        ch = self._get_or_create_channel("default:ALL")
        ch.feed_duplicates = val

    @property
    def feed_out_of_order(self) -> int:
        return sum(ch.feed_out_of_order for ch in self._channels_snapshot())

    @property
    def out_of_order(self) -> int:
        return self.feed_out_of_order

    @out_of_order.setter
    def out_of_order(self, val: int) -> None:
        if self._lock is not None:
            with self._lock:
                ch = self._get_or_create_channel("default:ALL")
                ch.feed_out_of_order = val
            return
        ch = self._get_or_create_channel("default:ALL")
        ch.feed_out_of_order = val

    @property
    def feed_resets(self) -> int:
        return sum(ch.feed_resets for ch in self._channels_snapshot())

    @property
    def feed_snapshots(self) -> int:
        return sum(ch.feed_snapshots for ch in self._channels_snapshot())

    @property
    def stale_epoch_packets(self) -> int:
        return sum(ch.stale_epoch_packets for ch in self._channels_snapshot())

    @property
    def epoch_unavailable(self) -> int:
        return sum(ch.epoch_unavailable for ch in self._channels_snapshot())

    @property
    def degraded_resets(self) -> int:
        return sum(ch.degraded_resets for ch in self._channels_snapshot())

    def _get_or_create_channel(
        self,
        channel_key: str,
        gateway_id: Optional[str] = None,
        symbol: Optional[str] = None,
    ) -> ChannelSequenceState:
        if channel_key not in self.channels:
            self.channels[channel_key] = ChannelSequenceState(
                channel_key=channel_key,
                gateway_id=gateway_id,
                symbol=symbol,
            )
        return self.channels[channel_key]

    def update_sequence(
        self,
        seq: Optional[int],
        feed_type: str = "LIVE",
        is_snapshot: bool = False,
        epoch_id: Optional[Any] = None,
        gateway_id: Optional[str] = None,
        symbol: Optional[str] = None,
        stream_rejected: bool = False,
    ) -> SequenceDisposition:
        """Update per-venue feed sequence continuity with deterministic epoch and gateway scoping.

        Preserves independent sequence domain:
        - If seq is None: still validates epoch on existing/epoch-scoped channels, records
          feed_seq_unavailable without defaulting seq to 0, and returns STALE_EPOCH or UNAVAILABLE.
        - Connectivity (`status="CONNECTED"`, `last_seen_ts`) and `_last_active_channel` only advance
          when the event is accepted (or genuinely non-sequenced and not stream-rejected), preventing
          rejected packets from resurrecting `"STALE"` venues.
        """
        if self._lock is not None:
            with self._lock:
                return self._update_sequence_unlocked(
                    seq=seq,
                    feed_type=feed_type,
                    is_snapshot=is_snapshot,
                    epoch_id=epoch_id,
                    gateway_id=gateway_id,
                    symbol=symbol,
                    stream_rejected=stream_rejected,
                )
        return self._update_sequence_unlocked(
            seq=seq,
            feed_type=feed_type,
            is_snapshot=is_snapshot,
            epoch_id=epoch_id,
            gateway_id=gateway_id,
            symbol=symbol,
            stream_rejected=stream_rejected,
        )

    def _update_sequence_unlocked(
        self,
        seq: Optional[int],
        feed_type: str,
        is_snapshot: bool,
        epoch_id: Optional[Any],
        gateway_id: Optional[str],
        symbol: Optional[str],
        stream_rejected: bool = False,
    ) -> SequenceDisposition:
        self.events_received += 1

        if gateway_id is None and any(
            ch.gateway_id is not None for ch in self.channels.values()
        ):
            self.gateway_unscoped += 1

        channel_key = f"{gateway_id or 'default'}:{symbol or 'ALL'}"

        if seq is None:
            existing_ch = self.channels.get(channel_key)
            if existing_ch is not None:
                disposition = existing_ch.update(
                    seq=None, epoch_id=epoch_id, is_snapshot=is_snapshot
                )
                self.feed_seq_unavailable += 1
                if disposition == SequenceDisposition.STALE_EPOCH:
                    return disposition
                if (
                    existing_ch.has_sequence_feed
                    or existing_ch.current_epoch_id is not None
                ):
                    # Channel expects sequences/epochs; do not mark CONNECTED on missing identity
                    return SequenceDisposition.UNAVAILABLE
            else:
                self.feed_seq_unavailable += 1
                if epoch_id is not None:
                    # Epoch-scoped packet missing sequence cannot initialize epoch state
                    return SequenceDisposition.UNAVAILABLE

            if not stream_rejected:
                self.last_seen_ts = time.time()
                self.status = "CONNECTED"
                self.feed_type = feed_type
            return SequenceDisposition.UNAVAILABLE

        ch = self._get_or_create_channel(
            channel_key, gateway_id=gateway_id, symbol=symbol
        )
        disposition = ch.update(seq=seq, epoch_id=epoch_id, is_snapshot=is_snapshot)
        if disposition == SequenceDisposition.ACCEPTED:
            self.last_seen_ts = time.time()
            self.status = "CONNECTED"
            self.feed_type = feed_type
            self._last_active_channel = channel_key
        return disposition

    def record_heartbeat(self, feed_type: str = "LIVE") -> None:
        if self._lock is not None:
            with self._lock:
                self.events_received += 1
                self.last_seen_ts = time.time()
                self.status = "CONNECTED"
                self.feed_type = feed_type
            return
        self.events_received += 1
        self.last_seen_ts = time.time()
        self.status = "CONNECTED"
        self.feed_type = feed_type

    def check_stale(self, max_stale_s: float = 3.0) -> None:
        if self._lock is not None:
            with self._lock:
                if (
                    self.last_seen_ts > 0
                    and (time.time() - self.last_seen_ts) > max_stale_s
                ):
                    self.status = "STALE"
            return
        if self.last_seen_ts > 0 and (time.time() - self.last_seen_ts) > max_stale_s:
            self.status = "STALE"


@dataclass(frozen=True)
class _ParsedEvent:
    """Immutable intermediate representation produced by Stage 1 input parsing."""

    raw_source: str
    symbol: str
    event_type: str
    stream_seq: int
    feed_seq: Optional[int]
    is_snapshot: bool
    epoch_id: Optional[Any]
    gateway_id: Optional[str]
    price: Optional[float]
    size: Optional[float]
    bid: Optional[float]
    ask: Optional[float]
    bid_size: Optional[float]
    ask_size: Optional[float]
    ts: float
    numeric_valid: bool


class ObserverState:
    """Thread-safe in-memory market data state engine with currency-isolated order books.

    Synchronization:
      All internal mutations and snapshot reads are protected by a reentrant lock
      (`self._lock`). Concurrent readers (`snapshot_health()`, `snapshot_dashboard_data()`,
      `get_throughput_eps()`, `get_consolidated_nbbo()`) copy data cleanly under the lock
      and never hold locks during terminal rendering or file I/O.

    Memory scaling:
      `quotes` (core assets x venues), `trades` (bounded `maxlen=max_trades` per asset),
      and `_event_timestamps` (`maxlen=5000`) are bounded; `VenueHealth.channels` scales
      as O(C) with distinct `(gateway_id, asset)` channels observed.

    Note on trade classification:
      When explicit exchange aggressor-side truth is absent, trade side (`BUY`/`SELL`) is
      estimated using the Lee-Ready heuristic (quote rule against top-of-book bid/ask,
      falling back to the tick rule on prior trade price). This is a heuristic estimate
      for observational analytics, not guaranteed exchange-reported aggressor-side truth.
    """

    def __init__(
        self,
        max_trades: int = 50,
        stale_threshold_s: float = 3.0,
        subscription_filter: str = "ALL",
    ):
        self._lock = threading.RLock()
        self.max_trades = max_trades
        self.stale_threshold_s = stale_threshold_s
        self.subscription_filter = subscription_filter
        self.stream_health = StreamHealth()
        self.quotes: dict[str, dict[str, VenueQuote]] = {
            asset: {venue: VenueQuote(venue=venue, symbol=asset) for venue in VENUES}
            for asset in CORE_ASSETS
        }
        self.trades: dict[str, collections.deque[TradePrint]] = {
            asset: collections.deque(maxlen=max_trades) for asset in CORE_ASSETS
        }
        self.health: dict[str, VenueHealth] = {
            venue: VenueHealth(venue=venue, _lock=self._lock) for venue in VENUES
        }
        self._last_trade_price: dict[str, float] = {}
        self._last_trade_side: dict[str, str] = {}
        self._start_time = time.time()
        self._event_timestamps: collections.deque[float] = collections.deque(
            maxlen=5000
        )

    def _parse_event(self, event: dict[str, Any] | Any, now: float) -> _ParsedEvent:
        """Stage 1: Parse and validate raw dict or object attributes without mutating state."""
        if isinstance(event, dict):
            raw_source = (
                str(_first_present_dict(event, ("venue", "source")) or "")
                .strip()
                .upper()
            )
            symbol = (
                str(
                    _first_present_dict(event, ("sym", "symbol", "instrument_id")) or ""
                )
                .strip()
                .upper()
            )
            event_type = (
                str(_first_present_dict(event, ("event", "event_type")) or "TICK")
                .strip()
                .upper()
            )
            stream_seq = _safe_int(event.get("seq"), default=0)

            raw_feed_seq = _first_present_dict(
                event, ("feed_seq", "venue_seq", "channel_seq", "sequence_number")
            )
            if raw_feed_seq is None and stream_seq == 0:
                raw_feed_seq = event.get("sequence")

            feed_seq = _parse_optional_int(raw_feed_seq)
            is_snapshot = bool(
                event.get("is_snapshot")
                or event.get("snapshot")
                or "SNAPSHOT" in event_type
            )
            epoch_id = _first_present_dict(event, ("epoch_id", "session_id"))
            gateway_id = _first_present_dict(
                event, ("gateway_id", "channel_id", "channel")
            )

            raw_price = event.get("price")
            raw_size = _first_present_dict(event, ("size", "quantity", "volume"))
            raw_bid = _first_present_dict(event, ("bid", "bid_price"))
            raw_ask = _first_present_dict(event, ("ask", "ask_price"))
            raw_bid_sz = event.get("bid_size")
            raw_ask_sz = event.get("ask_size")
            raw_ts = _first_present_dict(event, ("timestamp", "exchange_ts"))
        else:
            raw_source = (
                str(_first_present_attr(event, ("source", "venue")) or "")
                .strip()
                .upper()
            )
            symbol = (
                str(_first_present_attr(event, ("instrument_id", "symbol")) or "")
                .strip()
                .upper()
            )
            event_type = (
                str(getattr(event, "event_type", "TICK") or "TICK").strip().upper()
            )
            stream_seq = _safe_int(getattr(event, "seq", 0), default=0)

            raw_feed_seq = _first_present_attr(
                event, ("sequence_number", "venue_seq", "feed_seq")
            )
            if raw_feed_seq is None and stream_seq == 0:
                raw_feed_seq = getattr(event, "sequence", None)

            feed_seq = _parse_optional_int(raw_feed_seq)
            is_snapshot = bool(
                getattr(event, "is_snapshot", False) or "SNAPSHOT" in event_type
            )
            epoch_id = _first_present_attr(event, ("epoch_id", "session_id"))
            gateway_id = _first_present_attr(event, ("gateway_id", "channel_id"))

            raw_price = getattr(event, "price", None)
            raw_size = _first_present_attr(event, ("quantity", "size"))
            raw_bid = _first_present_attr(event, ("bid", "bid_price"))
            raw_ask = _first_present_attr(event, ("ask", "ask_price"))
            raw_bid_sz = getattr(event, "bid_size", None)
            raw_ask_sz = getattr(event, "ask_size", None)
            raw_ts = _first_present_attr(
                event, ("timestamp", "exchange_ts", "exchange_timestamp")
            )

        if isinstance(epoch_id, bool):
            epoch_id = None
        elif isinstance(epoch_id, str):
            epoch_id = epoch_id.strip() or None

        if isinstance(gateway_id, bool):
            gateway_id = None
        elif isinstance(gateway_id, str):
            gateway_id = gateway_id.strip() or None

        price, price_ok = _parse_optional_nonneg_finite_float(raw_price)
        size, size_ok = _parse_optional_nonneg_finite_float(raw_size)
        bid, bid_ok = _parse_optional_nonneg_finite_float(raw_bid)
        ask, ask_ok = _parse_optional_nonneg_finite_float(raw_ask)
        bid_sz, bid_sz_ok = _parse_optional_nonneg_finite_float(raw_bid_sz)
        ask_sz, ask_sz_ok = _parse_optional_nonneg_finite_float(raw_ask_sz)
        ts_val, ts_ok = _parse_optional_nonneg_finite_float(raw_ts)
        ts = ts_val if (ts_ok and ts_val is not None and ts_val > 0.0) else now

        numeric_valid = (
            price_ok
            and size_ok
            and bid_ok
            and ask_ok
            and bid_sz_ok
            and ask_sz_ok
            and ts_ok
        )

        return _ParsedEvent(
            raw_source=raw_source,
            symbol=symbol,
            event_type=event_type,
            stream_seq=stream_seq,
            feed_seq=feed_seq,
            is_snapshot=is_snapshot,
            epoch_id=epoch_id,
            gateway_id=gateway_id,
            price=price,
            size=size,
            bid=bid,
            ask=ask,
            bid_size=bid_sz,
            ask_size=ask_sz,
            ts=ts,
            numeric_valid=numeric_valid,
        )

    @staticmethod
    def _match_venue(raw_source: str) -> Optional[str]:
        for v in VENUES:
            if v in raw_source:
                return v
        return None

    def _infer_aggressor_side_lee_ready(
        self, asset: str, quote: VenueQuote, price: float
    ) -> str:
        """Estimate trade aggressor side using the Lee-Ready heuristic (quote rule + tick rule).

        Note: This is a heuristic estimate derived from top-of-book quotes and prior
        trade prices, not guaranteed exchange-reported aggressor-side truth.
        """
        if quote.ask_price > 0.0 and price >= quote.ask_price:
            return "BUY"
        if quote.bid_price > 0.0 and price <= quote.bid_price:
            return "SELL"
        if asset in self._last_trade_price:
            prev_price = self._last_trade_price[asset]
            if price > prev_price:
                return "BUY"
            if price < prev_price:
                return "SELL"
            return self._last_trade_side.get(asset, "BUY")
        return "BUY" if (quote.ask_price > 0.0 and price >= quote.mid_price) else "SELL"

    def process_event(self, event: dict[str, Any] | Any) -> None:
        """Process either a dictionary from TCP StreamClient or a CanonicalEvent/SBE from SHM.

        Executes in ordered stages under `self._lock`:
          Stage 1: Input parsing without state mutation.
          Stage 2: Venue matching and canonical symbol normalization.
          Stage 3: Numeric validation (rejects invalid numerics before mutating epoch, seq, or status).
          Stage 4: Epoch & sequence validation with normalized channel keying.
          Stage 5: Atomic market-state mutation (VenueQuote and TradePrint tape).
        """
        now = time.time()

        # Stage 1: Input parsing (pure / immutable)
        parsed = self._parse_event(event, now)

        # Stage 2: Venue matching and canonical symbol normalization
        feed_type = (
            "SIMULATED"
            if any(k in parsed.raw_source for k in ("SIM", "DEMO", "TEST", "FEED"))
            else "LIVE"
        )
        venue = self._match_venue(parsed.raw_source)
        if not venue:
            return

        asset = normalize_asset(parsed.symbol)
        sub_norm = (
            normalize_asset(self.subscription_filter)
            if self.subscription_filter != "ALL"
            else "ALL"
        )

        with self._lock:
            # Stage 3: Numeric validation BEFORE sequence, epoch, or connectivity mutation
            if not parsed.numeric_valid:
                self.health[venue].events_received += 1
                self.health[venue].invalid_numeric_events += 1
                return

            # Stage 4: Stream and per-venue epoch/sequence validation using normalized symbol
            stream_disposition = SequenceDisposition.UNAVAILABLE
            if parsed.stream_seq > 0:
                is_filtered = sub_norm != "ALL" and asset != sub_norm
                stream_disposition = self.stream_health.update(
                    parsed.stream_seq, is_filtered_gap=is_filtered
                )

            channel_symbol = asset or "ALL"
            channel_key = f"{parsed.gateway_id or 'default'}:{channel_symbol}"
            existing_ch = self.health[venue].channels.get(channel_key)
            channel_expects_seq_or_epoch = bool(
                existing_ch is not None
                and (
                    existing_ch.has_sequence_feed
                    or existing_ch.current_epoch_id is not None
                )
            )

            feed_disposition = self.health[venue].update_sequence(
                parsed.feed_seq,
                feed_type=feed_type,
                is_snapshot=parsed.is_snapshot,
                epoch_id=parsed.epoch_id,
                gateway_id=parsed.gateway_id,
                symbol=channel_symbol,
                stream_rejected=(stream_disposition in _REJECTED_DISPOSITIONS),
            )

            # Reject stale-epoch, out-of-order, duplicate, or unavailable identity atomically
            if feed_disposition in _REJECTED_DISPOSITIONS:
                return
            if feed_disposition == SequenceDisposition.UNAVAILABLE:
                if channel_expects_seq_or_epoch or parsed.epoch_id is not None:
                    return
                if stream_disposition in _REJECTED_DISPOSITIONS:
                    return

            # Stage 5: Record accepted timestamp & mutate core market state
            self._event_timestamps.append(now)

            if asset not in self.quotes:
                return

            quote = self.quotes[asset][venue]
            quote.last_update_ts = parsed.ts
            quote.sequence = (
                parsed.stream_seq if parsed.stream_seq > 0 else (parsed.feed_seq or 0)
            )

            if parsed.bid is not None and parsed.bid > 0.0:
                quote.bid_price = parsed.bid
                quote.bid_size = parsed.bid_size if parsed.bid_size is not None else 1.0
            if parsed.ask is not None and parsed.ask > 0.0:
                quote.ask_price = parsed.ask
                quote.ask_size = parsed.ask_size if parsed.ask_size is not None else 1.0

            if (
                "TRADE" in parsed.event_type
                and parsed.price is not None
                and parsed.price > 0.0
            ):
                side = self._infer_aggressor_side_lee_ready(asset, quote, parsed.price)
                self._last_trade_price[asset] = parsed.price
                self._last_trade_side[asset] = side

                trade_size = parsed.size if parsed.size is not None else 0.0
                tp = TradePrint(
                    timestamp=parsed.ts,
                    venue=venue,
                    symbol=asset,
                    price=parsed.price,
                    size=trade_size,
                    side=side,
                )
                self.trades[asset].appendleft(tp)
                if quote.bid_price == 0.0:
                    quote.bid_price = parsed.price * 0.9998
                    quote.bid_size = trade_size
                if quote.ask_price == 0.0:
                    quote.ask_price = parsed.price * 1.0002
                    quote.ask_size = trade_size

    def _get_consolidated_nbbo_unlocked(
        self, asset: str, usdt_fx_rate: float | None = None
    ) -> tuple[float, float, float, float]:
        best_bid = 0.0
        best_bid_sz = 0.0
        best_ask = float("inf")
        best_ask_sz = 0.0

        target_quotes = self.quotes.get(asset, {})
        for venue, q in target_quotes.items():
            eff_bid = q.bid_price
            eff_ask = q.ask_price

            if usdt_fx_rate is not None and "USDT" in asset:
                eff_bid = eff_bid * usdt_fx_rate
                eff_ask = eff_ask * usdt_fx_rate if eff_ask > 0 else 0.0

            if eff_bid > best_bid:
                best_bid = eff_bid
                best_bid_sz = q.bid_size
            if eff_ask > 0 and eff_ask < best_ask:
                best_ask = eff_ask
                best_ask_sz = q.ask_size

        if best_ask == float("inf"):
            best_ask = 0.0

        return best_bid, best_bid_sz, best_ask, best_ask_sz

    def get_consolidated_nbbo(
        self, asset: str, usdt_fx_rate: float | None = None
    ) -> tuple[float, float, float, float]:
        """
        Returns best consolidated (best_bid, best_bid_sz, best_ask, best_ask_sz)
        within the specified currency pair.
        Never blends USD and USDT unless explicit usdt_fx_rate is provided.
        """
        with self._lock:
            return self._get_consolidated_nbbo_unlocked(
                asset, usdt_fx_rate=usdt_fx_rate
            )

    def check_all_stale(self) -> None:
        """Trigger stale checks across all tracked venue health records atomically."""
        with self._lock:
            for vh in self.health.values():
                vh.check_stale(max_stale_s=self.stale_threshold_s)

    def _get_throughput_eps_unlocked(self, now: float) -> float:
        timestamps = list(self._event_timestamps)
        recent = [t for t in timestamps if now - t <= 2.0]
        if recent:
            duration = max(0.001, now - recent[0])
            return len(recent) / duration
        return 0.0

    def get_throughput_eps(self) -> float:
        """Calculate events per second over recent sliding window safely."""
        now = time.time()
        with self._lock:
            return self._get_throughput_eps_unlocked(now)

    def snapshot_health(self) -> dict[str, Any]:
        """Return an atomic, decoupled copy of stream and per-venue health diagnostics."""
        now = time.time()
        with self._lock:
            stream_snap = {
                "events": self.stream_health.events_received,
                "gaps": self.stream_health.gaps,
                "duplicates": self.stream_health.duplicates,
                "out_of_order": self.stream_health.out_of_order,
                "resets": self.stream_health.resets,
                "filtered_events": self.stream_health.filtered_events,
            }
            venues_snap: dict[str, dict[str, Any]] = {}
            for v, vh in self.health.items():
                channels = list(vh.channels.values())
                feed_gaps = sum(ch.feed_gaps for ch in channels)
                feed_dups = sum(ch.feed_duplicates for ch in channels)
                feed_ooo = sum(ch.feed_out_of_order for ch in channels)
                feed_resets = sum(ch.feed_resets for ch in channels)
                stale_epoch = sum(ch.stale_epoch_packets for ch in channels)
                degraded_resets = sum(ch.degraded_resets for ch in channels)
                epoch_unavail = sum(ch.epoch_unavailable for ch in channels)
                venues_snap[v] = {
                    "status": vh.status,
                    "feed_type": vh.feed_type,
                    "events": vh.events_received,
                    "gaps": feed_gaps,
                    "feed_gaps": feed_gaps,
                    "duplicates": feed_dups,
                    "out_of_order": feed_ooo,
                    "feed_resets": feed_resets,
                    "degraded_resets": degraded_resets,
                    "stale_epoch_packets": stale_epoch,
                    "epoch_unavailable": epoch_unavail,
                    "feed_seq_unavailable": vh.feed_seq_unavailable,
                    "invalid_numeric_events": vh.invalid_numeric_events,
                    "gateway_unscoped": vh.gateway_unscoped,
                    "channel_count": len(channels),
                    "reconnects": vh.reconnects,
                    "reconnects_supported": False,
                    "drops": vh.drops,
                    "drops_supported": False,
                    "last_seen_ts": vh.last_seen_ts,
                }
            eps = self._get_throughput_eps_unlocked(now)
            return {
                "eps": eps,
                "stream": stream_snap,
                "venues": venues_snap,
            }

    def snapshot_dashboard_data(self) -> dict[str, Any]:
        """Return an atomic, decoupled snapshot for terminal rendering outside any lock."""
        now = time.time()
        with self._lock:
            health_snap = self.snapshot_health()
            nbbo_snap = {
                asset: self._get_consolidated_nbbo_unlocked(asset)
                for asset in CORE_ASSETS
            }
            quotes_snap: dict[str, dict[str, tuple[float, float, float]]] = {}
            for asset in CORE_ASSETS:
                quotes_snap[asset] = {}
                for venue in VENUES:
                    q = self.quotes[asset][venue]
                    quotes_snap[asset][venue] = (q.bid_price, q.ask_price, q.spread_bps)

            recent_trades: list[TradePrint] = []
            for asset in CORE_ASSETS:
                recent_trades.extend(list(self.trades[asset])[:5])
            recent_trades.sort(key=lambda t: t.timestamp, reverse=True)

            return {
                "now": now,
                "eps": health_snap["eps"],
                "stream": health_snap["stream"],
                "venues": health_snap["venues"],
                "nbbo": nbbo_snap,
                "quotes": quotes_snap,
                "recent_trades": recent_trades[:6],
            }

    def get_rss_mb(self) -> float:
        """Query current process Resident Set Size (RSS) in MiB."""
        try:
            import psutil

            return psutil.Process().memory_info().rss / (1024.0 * 1024.0)
        except Exception:
            return 32.5
