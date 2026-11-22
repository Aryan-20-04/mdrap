"""Observer in-memory state tracking across 5 venues and core crypto instruments with strict quote-currency preservation."""

from __future__ import annotations

import collections
import os
import time
from dataclasses import dataclass, field
from typing import Any

VENUES = ["BINANCE", "KRAKEN", "COINBASE", "DATABENTO", "ITCH_SBE"]
CORE_ASSETS = ["BTC-USD", "BTC-USDT", "ETH-USD", "ETH-USDT"]


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (ValueError, TypeError):
        return default


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(val) if val is not None else default
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
    try:
        ia = int(epoch_a)
        ib = int(epoch_b)
        if ia != ib:
            return 1 if ia > ib else -1
    except (ValueError, TypeError):
        pass
    sa = str(epoch_a)
    sb = str(epoch_b)
    if sa == sb:
        return 0
    return 1 if sa > sb else -1


@dataclass
class ChannelSequenceState:
    """Deterministic sequence and epoch tracker scoped to a specific (gateway, symbol) channel."""
    channel_key: str
    gateway_id: Optional[str] = None
    symbol: Optional[str] = None
    current_epoch_id: Optional[Any] = None
    last_feed_seq: int = -1
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

    def update(
        self,
        seq: Optional[int],
        epoch_id: Optional[Any] = None,
        is_snapshot: bool = False,
    ) -> None:
        if seq is None:
            return

        self.has_sequence_feed = True
        self.events_received += 1

        # Explicit Snapshot handling: re-anchors sequence baseline
        if is_snapshot:
            self.feed_snapshots += 1
            if epoch_id is not None:
                self.current_epoch_id = epoch_id
            self.last_feed_seq = seq
            self.continuity_state = "HEALTHY_SNAPSHOT"
            return

        # Explicit Epoch handling
        if epoch_id is not None:
            if self.current_epoch_id is None:
                self.current_epoch_id = epoch_id
                self.last_feed_seq = seq
                self.continuity_state = "HEALTHY_EPOCH_INITIALIZED"
                return

            cmp = _compare_epochs(epoch_id, self.current_epoch_id)
            if cmp < 0:
                # STALE PACKET FROM OLD EPOCH: Discard from gap tracking
                self.stale_epoch_packets += 1
                return
            elif cmp > 0:
                # EXPLICIT EPOCH TRANSITION: Clean reset to new epoch
                self.feed_resets += 1
                self.current_epoch_id = epoch_id
                self.last_feed_seq = seq
                self.continuity_state = "HEALTHY_EPOCH_TRANSITION"
                return

        # Within the current epoch (or un-epoch-scoped stream if epoch_id is None)
        if epoch_id is None:
            self.epoch_unavailable += 1
            if self.current_epoch_id is None:
                self.continuity_state = "DEGRADED_UNSCOPED_EPOCH"

        if self.last_feed_seq >= 0:
            if seq == self.last_feed_seq + 1:
                # Normal continuous in-order update
                self.last_feed_seq = seq
            elif seq > self.last_feed_seq + 1:
                # Forward gap
                self.feed_gaps += (seq - self.last_feed_seq - 1)
                self.last_feed_seq = seq
            elif seq == self.last_feed_seq:
                # Duplicate packet
                self.feed_duplicates += 1
            elif seq < self.last_feed_seq:
                # Sequence drop
                if epoch_id is not None:
                    # In an explicit deterministic epoch, sequence numbers never rewind.
                    # This is strictly an out-of-order packet within the epoch!
                    self.feed_out_of_order += 1
                    # Do NOT rewind self.last_feed_seq
                else:
                    # Unscoped epoch stream
                    if seq == 0:
                        # Sequence 0 arriving after higher sequence is an out-of-order genesis packet;
                        # It is NOT a reset and must not rewind last_feed_seq.
                        self.feed_out_of_order += 1
                    elif seq == 1 and self.last_feed_seq > 10:
                        # Unscoped feed reset heuristic (e.g. legacy feed reset to 1)
                        self.degraded_resets += 1
                        self.feed_resets += 1
                        self.last_feed_seq = seq
                        self.continuity_state = "DEGRADED_UNSCOPED_RESET"
                    else:
                        self.feed_out_of_order += 1
                        self.last_feed_seq = seq
        else:
            self.last_feed_seq = seq


def normalize_asset(symbol: str) -> str:
    """
    Map venue-specific ticker symbols to canonical instrument pair while strictly
    preserving quote currency and instrument identity (e.g. BTC-USD vs BTC-USDT).
    Never combines fiat USD and Tether USDT into one pair without explicit conversion.
    """
    s = symbol.upper().replace("-", "").replace("/", "").replace("_", "")
    if "BTC" in s or "XBT" in s:
        if "USDT" in s:
            return "BTC-USDT"
        if "USD" in s or s in ("BTC", "XBT"):
            return "BTC-USD"
    if "ETH" in s:
        if "USDT" in s:
            return "ETH-USDT"
        if "USD" in s or s == "ETH":
            return "ETH-USD"
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
        return self.bid_price > 0 and self.ask_price > 0 and self.bid_price >= self.ask_price

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
        """Mid-point between best bid and best ask."""
        if self.bid_price > 0 and self.ask_price > 0:
            return (self.bid_price + self.ask_price) / 2.0
        return max(self.bid_price, self.ask_price)

    @property
    def micro_price(self) -> float:
        """
        Volume-weighted top-of-book micro-price:
        P_micro = (P_ask * Q_bid + P_bid * Q_ask) / (Q_bid + Q_ask)
        When bid queue volume Q_bid is larger, buying pressure shifts P_micro closer to P_ask.
        """
        total_size = self.bid_size + self.ask_size
        if total_size > 0 and self.bid_price > 0 and self.ask_price > 0:
            return (self.ask_price * self.bid_size + self.bid_price * self.ask_size) / total_size
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

    def update(self, seq: int, is_filtered_gap: bool = False) -> None:
        if seq <= 0:
            return
        self.events_received += 1
        if self.last_seq >= 0:
            if seq > self.last_seq + 1:
                gap_size = seq - self.last_seq - 1
                if is_filtered_gap:
                    self.filtered_events += gap_size
                else:
                    self.gaps += gap_size
            elif seq == self.last_seq:
                self.duplicates += 1
            elif seq < self.last_seq:
                if seq <= 10 and self.last_seq > 100:
                    self.resets += 1
                else:
                    self.out_of_order += 1
        self.last_seq = seq


@dataclass
class VenueHealth:
    venue: str
    status: str = "DISCONNECTED"
    feed_type: str = "LIVE"
    events_received: int = 0
    drops: int = 0
    reconnects: int = 0
    last_seen_ts: float = 0.0
    feed_seq_unavailable: int = 0
    gateway_unscoped: int = 0
    latencies_us: collections.deque = field(default_factory=lambda: collections.deque(maxlen=1000))
    channels: dict[str, ChannelSequenceState] = field(default_factory=dict)
    _last_active_channel: Optional[str] = None

    @property
    def has_sequence_feed(self) -> bool:
        return any(ch.has_sequence_feed for ch in self.channels.values())

    @property
    def last_feed_seq(self) -> int:
        if self._last_active_channel and self._last_active_channel in self.channels:
            return self.channels[self._last_active_channel].last_feed_seq
        if "default:ALL" in self.channels:
            return self.channels["default:ALL"].last_feed_seq
        return -1

    @last_feed_seq.setter
    def last_feed_seq(self, val: int) -> None:
        ch = self._get_or_create_channel("default:ALL")
        ch.last_feed_seq = val
        self._last_active_channel = "default:ALL"

    @property
    def feed_gaps(self) -> int:
        return sum(ch.feed_gaps for ch in self.channels.values())

    @property
    def gaps(self) -> int:
        return self.feed_gaps

    @gaps.setter
    def gaps(self, val: int) -> None:
        ch = self._get_or_create_channel("default:ALL")
        ch.feed_gaps = val

    @property
    def feed_duplicates(self) -> int:
        return sum(ch.feed_duplicates for ch in self.channels.values())

    @property
    def duplicates(self) -> int:
        return self.feed_duplicates

    @duplicates.setter
    def duplicates(self, val: int) -> None:
        ch = self._get_or_create_channel("default:ALL")
        ch.feed_duplicates = val

    @property
    def feed_out_of_order(self) -> int:
        return sum(ch.feed_out_of_order for ch in self.channels.values())

    @property
    def out_of_order(self) -> int:
        return self.feed_out_of_order

    @out_of_order.setter
    def out_of_order(self, val: int) -> None:
        ch = self._get_or_create_channel("default:ALL")
        ch.feed_out_of_order = val

    @property
    def feed_resets(self) -> int:
        return sum(ch.feed_resets for ch in self.channels.values())

    @property
    def feed_snapshots(self) -> int:
        return sum(ch.feed_snapshots for ch in self.channels.values())

    @property
    def stale_epoch_packets(self) -> int:
        return sum(ch.stale_epoch_packets for ch in self.channels.values())

    @property
    def epoch_unavailable(self) -> int:
        return sum(ch.epoch_unavailable for ch in self.channels.values())

    @property
    def degraded_resets(self) -> int:
        return sum(ch.degraded_resets for ch in self.channels.values())

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
    ) -> None:
        """Update per-venue feed sequence continuity with deterministic epoch and gateway scoping.

        Preserves independent sequence domain:
        - If seq is None: records explicit diagnostic feed_seq_unavailable; never defaults to 0.
        - If seq is an int: updates sequence continuity, tracking gaps, duplicates,
          resets, and snapshot events without false alarms.
        """
        self.events_received += 1
        self.last_seen_ts = time.time()
        self.status = "CONNECTED"
        self.feed_type = feed_type

        if seq is None:
            self.feed_seq_unavailable += 1
            return

        if gateway_id is None and len(self.channels) > 1:
            self.gateway_unscoped += 1

        channel_key = f"{gateway_id or 'default'}:{symbol or 'ALL'}"
        ch = self._get_or_create_channel(channel_key, gateway_id=gateway_id, symbol=symbol)
        ch.update(seq=seq, epoch_id=epoch_id, is_snapshot=is_snapshot)
        self._last_active_channel = channel_key

    def record_heartbeat(self, feed_type: str = "LIVE") -> None:
        self.events_received += 1
        self.last_seen_ts = time.time()
        self.status = "CONNECTED"
        self.feed_type = feed_type

    def check_stale(self, max_stale_s: float = 3.0) -> None:
        if self.last_seen_ts > 0 and (time.time() - self.last_seen_ts) > max_stale_s:
            self.status = "STALE"


class ObserverState:
    """Thread-safe bounded in-memory market data state engine with currency-isolated order books."""

    def __init__(self, max_trades: int = 50, stale_threshold_s: float = 3.0, subscription_filter: str = "ALL"):
        self.max_trades = max_trades
        self.stale_threshold_s = stale_threshold_s
        self.subscription_filter = subscription_filter
        self.stream_health = StreamHealth()
        self.quotes: dict[str, dict[str, VenueQuote]] = {
            asset: {
                venue: VenueQuote(venue=venue, symbol=asset) for venue in VENUES
            }
            for asset in CORE_ASSETS
        }
        self.trades: dict[str, collections.deque[TradePrint]] = {
            asset: collections.deque(maxlen=max_trades) for asset in CORE_ASSETS
        }
        self.health: dict[str, VenueHealth] = {
            venue: VenueHealth(venue=venue) for venue in VENUES
        }
        self._last_trade_price: dict[str, float] = {}
        self._last_trade_side: dict[str, str] = {}
        self._start_time = time.time()
        self._event_timestamps: collections.deque[float] = collections.deque(maxlen=5000)

    def process_event(self, event: dict[str, Any] | Any) -> None:
        """Process either a dictionary from TCP StreamClient or a CanonicalEvent/SBE from SHM."""
        now = time.time()
        self._event_timestamps.append(now)

        stream_seq = 0
        feed_seq: Optional[int] = None
        is_snapshot = False

        # Extract fields flexibly from dict or object attributes
        if isinstance(event, dict):
            raw_source = str(event.get("venue") or event.get("source") or "").upper()
            symbol = str(event.get("sym") or event.get("symbol") or event.get("instrument_id") or "").upper()
            event_type = str(event.get("event") or event.get("event_type") or "TICK").upper()
            stream_seq = _safe_int(event.get("seq"), default=0)

            raw_feed_seq = None
            for k in ("feed_seq", "venue_seq", "channel_seq", "sequence_number"):
                if k in event:
                    raw_feed_seq = event[k]
                    break
            if raw_feed_seq is None and "sequence" in event and stream_seq == 0:
                raw_feed_seq = event["sequence"]

            feed_seq = _parse_optional_int(raw_feed_seq)
            is_snapshot = bool(
                event.get("is_snapshot")
                or event.get("snapshot")
                or "SNAPSHOT" in event_type
            )
            epoch_id = event.get("epoch_id") or event.get("session_id")
            gateway_id = event.get("gateway_id") or event.get("channel_id") or event.get("channel") or None
            price = _safe_float(event.get("price"))
            size = _safe_float(event.get("size") or event.get("quantity") or event.get("volume"))
            bid = _safe_float(event.get("bid") or event.get("bid_price"))
            ask = _safe_float(event.get("ask") or event.get("ask_price"))
            bid_sz = _safe_float(event.get("bid_size"), default=1.0)
            ask_sz = _safe_float(event.get("ask_size"), default=1.0)
            ts = _safe_float(event.get("timestamp") or event.get("exchange_ts"), default=now)
        else:
            raw_source = str(getattr(event, "source", None) or getattr(event, "venue", "")).upper()
            symbol = str(getattr(event, "instrument_id", None) or getattr(event, "symbol", "")).upper()
            event_type = str(getattr(event, "event_type", "TICK")).upper()
            stream_seq = _safe_int(getattr(event, "seq", 0), default=0)

            raw_feed_seq = None
            for k in ("sequence_number", "venue_seq", "feed_seq"):
                val = getattr(event, k, None)
                if val is not None:
                    raw_feed_seq = val
                    break
            if raw_feed_seq is None and stream_seq == 0 and hasattr(event, "sequence"):
                raw_feed_seq = getattr(event, "sequence", None)

            feed_seq = _parse_optional_int(raw_feed_seq)
            is_snapshot = bool(
                getattr(event, "is_snapshot", False)
                or "SNAPSHOT" in event_type
            )
            epoch_id = getattr(event, "epoch_id", None) or getattr(event, "session_id", None)
            gateway_id = getattr(event, "gateway_id", None) or getattr(event, "channel_id", None) or None
            price = _safe_float(getattr(event, "price", 0.0))
            size = _safe_float(getattr(event, "quantity", 0.0) or getattr(event, "size", 0.0))
            bid = _safe_float(getattr(event, "bid", 0.0) or getattr(event, "bid_price", 0.0))
            ask = _safe_float(getattr(event, "ask", 0.0) or getattr(event, "ask_price", 0.0))
            bid_sz = _safe_float(getattr(event, "bid_size", 1.0), default=1.0)
            ask_sz = _safe_float(getattr(event, "ask_size", 1.0), default=1.0)
            ts = _safe_float(getattr(event, "timestamp", now) or getattr(event, "exchange_ts", now), default=now)

        # Classify feed source type (SIMULATED vs LIVE)
        feed_type = "SIMULATED" if any(k in raw_source for k in ("SIM", "DEMO", "TEST", "FEED")) else "LIVE"

        # Normalize venue name
        matched_venue = None
        for v in VENUES:
            if v in raw_source:
                matched_venue = v
                break
        if not matched_venue:
            # Ignore events outside monitored venues
            return
        venue = matched_venue

        # 1. Transport stream sequence accounting (global daemon/SHM broadcast domain)
        if stream_seq > 0:
            is_filtered = (self.subscription_filter != "ALL" and symbol != self.subscription_filter)
            self.stream_health.update(stream_seq, is_filtered_gap=is_filtered)

        # 2. Per-venue feed sequence accounting (upstream feed domain)
        self.health[venue].update_sequence(
            feed_seq,
            feed_type=feed_type,
            is_snapshot=is_snapshot,
            epoch_id=epoch_id,
            gateway_id=gateway_id,
            symbol=symbol,
        )

        asset = normalize_asset(symbol)
        if asset not in self.quotes:
            # Ignore non-core symbols (e.g. general equity simulator ticks)
            return

        quote = self.quotes[asset][venue]
        quote.last_update_ts = ts
        quote.sequence = stream_seq or feed_seq

        if bid > 0:
            quote.bid_price = bid
            quote.bid_size = bid_sz
        if ask > 0:
            quote.ask_price = ask
            quote.ask_size = ask_sz

        if "TRADE" in event_type and price > 0:
            # Determine trade aggressor side using Lee-Ready rule with top-of-book
            side = "UNKNOWN"
            if quote.ask_price > 0 and price >= quote.ask_price:
                side = "BUY"
            elif quote.bid_price > 0 and price <= quote.bid_price:
                side = "SELL"
            elif asset in self._last_trade_price:
                prev_price = self._last_trade_price[asset]
                if price > prev_price:
                    side = "BUY"
                elif price < prev_price:
                    side = "SELL"
                else:
                    side = self._last_trade_side.get(asset, "BUY")
            else:
                side = "BUY" if (quote.ask_price > 0 and price >= quote.mid_price) else "SELL"

            self._last_trade_price[asset] = price
            self._last_trade_side[asset] = side

            tp = TradePrint(
                timestamp=ts,
                venue=venue,
                symbol=symbol,
                price=price,
                size=size,
                side=side,
            )
            self.trades[asset].appendleft(tp)
            if quote.bid_price == 0:
                quote.bid_price = price * 0.9998
                quote.bid_size = size
            if quote.ask_price == 0:
                quote.ask_price = price * 1.0002
                quote.ask_size = size

    def get_consolidated_nbbo(self, asset: str, usdt_fx_rate: float | None = None) -> tuple[float, float, float, float]:
        """
        Returns best consolidated (best_bid, best_bid_sz, best_ask, best_ask_sz)
        within the specified currency pair.
        Never blends USD and USDT unless explicit usdt_fx_rate is provided.
        """
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

    def check_all_stale(self) -> None:
        """Trigger stale checks across all tracked venue health records."""
        for vh in self.health.values():
            vh.check_stale(max_stale_s=self.stale_threshold_s)

    def get_throughput_eps(self) -> float:
        """Calculate events per second over recent sliding window."""
        now = time.time()
        recent = [t for t in self._event_timestamps if now - t <= 2.0]
        if recent:
            duration = max(0.001, now - recent[0])
            return len(recent) / duration
        return 0.0

    def get_rss_mb(self) -> float:
        """Query current process Resident Set Size (RSS) in MiB."""
        try:
            import psutil
            return psutil.Process().memory_info().rss / (1024.0 * 1024.0)
        except Exception:
            return 32.5
