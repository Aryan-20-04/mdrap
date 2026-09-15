"""
Deterministic feed simulator.

Generates trade/quote events for a fixed instrument universe from
multiple "sources" (as if the same market were seen through several
vendor feeds). Faults -- duplicates, missing events, out-of-order
delivery, malformed records, latency -- are injected under a fixed
random seed so any run can be replayed exactly and the injected error
set is known ground truth for quality-engine scoring.
"""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field
from typing import Iterator, List, Optional

from models import RawEvent


@dataclass
class SimulatorConfig:
    seed: int = 42
    num_events: int = 100_000
    instruments: List[str] = field(
        default_factory=lambda: [
            "AAPL",
            "MSFT",
            "GOOGL",
            "AMZN",
            "NVDA",
            "TSLA",
            "META",
            "JPM",
        ]
    )
    sources: List[str] = field(default_factory=lambda: ["FEEDX", "FEEDY", "FEEDZ"])
    start_price: float = 100.0
    market: str = "us"  # 'us', 'nse', 'xetra', 'tse', 'global'

    def __post_init__(self):
        m = (self.market or "us").lower()
        if m in ("nse", "india", "in"):
            self.instruments = [
                "RELIANCE.NS",
                "TCS.NS",
                "HDFCBANK.NS",
                "INFY.NS",
                "ICICIBANK.NS",
                "TATAMOTORS.NS",
            ]
            if self.start_price == 100.0:
                self.start_price = 2400.0
        elif m in ("xetra", "germany", "de", "eurex"):
            self.instruments = [
                "SAP.DE",
                "SIE.DE",
                "BMW.DE",
                "VOW3.DE",
                "ALV.DE",
                "MBG.DE",
            ]
            if self.start_price == 100.0:
                self.start_price = 140.0
        elif m in ("tse", "japan", "jp", "jpx"):
            self.instruments = ["7203.T", "6758.T", "9984.T", "8306.T", "6861.T"]
            if self.start_price == 100.0:
                self.start_price = 3200.0
        elif m in ("global", "world", "all"):
            self.instruments = [
                "AAPL",
                "NVDA",
                "RELIANCE.NS",
                "TCS.NS",
                "SAP.DE",
                "BMW.DE",
                "7203.T",
                "6758.T",
            ]
            if self.start_price == 100.0:
                self.start_price = 250.0

    duplicate_rate: float = 0.002
    missing_rate: float = 0.001  # sequence numbers skipped (never emitted)
    out_of_order_rate: float = (
        0.0005  # events emitted with a timestamp behind the last one
    )
    malformed_rate: float = 0.0005  # payload missing/garbage fields
    price_anomaly_rate: float = 0.0005  # deliberate extreme price spike
    crossed_quote_rate: float = 0.0003  # bid > ask

    quote_ratio: float = 0.5  # fraction of events that are QUOTE vs TRADE
    mean_latency_s: float = 0.0008  # simulated network delay, exchange->receive
    latency_jitter_s: float = 0.0006

    # Ground-truth counters get attached to the config instance after a run
    # (see FeedSimulator.injected) so the benchmark harness can score
    # detection/false-positive/false-negative rates against a known answer.


class _InstrumentState:
    __slots__ = ("price", "seq")

    def __init__(self, price: float):
        self.price = price
        self.seq = 0


class FeedSimulator:
    """Deterministic, replayable multi-source event generator.

    Usage:
        sim = FeedSimulator(SimulatorConfig(seed=42, num_events=1_000_000))
        for raw_event, truth_label in sim.generate():
            ...

    `truth_label` is None for normal events, or a Reason-like string
    naming the fault that was deliberately injected into that event --
    this is the ground truth used to score the quality engine.
    """

    def __init__(self, config: SimulatorConfig):
        self.config = config
        self.injected = {
            "duplicate": 0,
            "missing": 0,
            "out_of_order": 0,
            "malformed": 0,
            "price_anomaly": 0,
            "crossed_quote": 0,
        }

    def generate(self) -> Iterator[tuple]:
        cfg = self.config
        rng = random.Random(cfg.seed)
        state = {
            s: {i: _InstrumentState(cfg.start_price) for i in cfg.instruments}
            for s in cfg.sources
        }
        t = 1_700_000_000.0  # arbitrary fixed epoch start -> fully deterministic
        pending_duplicate: Optional[tuple] = None
        _id_counter = itertools.count(1)

        emitted = 0
        while emitted < cfg.num_events:
            # Replay a duplicate of the previous event if one is queued.
            if pending_duplicate is not None:
                raw, label = pending_duplicate
                pending_duplicate = None
                yield raw, label
                emitted += 1
                continue

            source = rng.choice(cfg.sources)
            instrument = rng.choice(cfg.instruments)
            st = state[source][instrument]
            st.seq += 1
            seq = st.seq
            t += rng.expovariate(1000.0)  # ~1000 events/sec arrival process

            is_quote = rng.random() < cfg.quote_ratio
            # random walk price
            st.price = max(0.01, st.price + rng.gauss(0, 0.05))
            price = round(st.price, 2)

            label = None
            exchange_ts = t

            # -- Missing event: skip emitting this sequence number entirely.
            if rng.random() < cfg.missing_rate:
                self.injected["missing"] += 1
                continue  # sequence number burned, nothing emitted -> a real gap

            # -- Out-of-order: emit with a timestamp earlier than what we
            # already sent for this (source, instrument).
            if rng.random() < cfg.out_of_order_rate:
                exchange_ts = exchange_ts - rng.uniform(0.05, 0.5)
                label = "out_of_order"
                self.injected["out_of_order"] += 1

            payload = {
                "instrument": instrument,
                "event_type": "QUOTE" if is_quote else "TRADE",
                "exchange_ts": exchange_ts,
                "sequence": seq,
            }
            if is_quote:
                spread = max(0.01, rng.gauss(0.03, 0.01))
                payload["bid"] = round(price - spread / 2, 2)
                payload["ask"] = round(price + spread / 2, 2)
                payload["bid_size"] = rng.randint(1, 500) * 100
                payload["ask_size"] = rng.randint(1, 500) * 100
                if rng.random() < cfg.crossed_quote_rate:
                    payload["bid"], payload["ask"] = (
                        payload["ask"] + 0.05,
                        payload["bid"],
                    )
                    label = "crossed_quote"
                    self.injected["crossed_quote"] += 1
            else:
                payload["price"] = price
                payload["quantity"] = rng.randint(1, 100) * 10
                if rng.random() < cfg.price_anomaly_rate:
                    direction = rng.choice([-1, 1])
                    payload["price"] = round(
                        price * (1 + direction * rng.uniform(0.15, 0.4)), 2
                    )
                    label = "price_anomaly"
                    self.injected["price_anomaly"] += 1

            # -- Malformed: corrupt/drop a required field.
            if rng.random() < cfg.malformed_rate:
                victim = rng.choice(["instrument", "exchange_ts", "sequence"])
                if victim == "instrument":
                    payload.pop("instrument", None)
                elif victim == "exchange_ts":
                    payload["exchange_ts"] = "not-a-timestamp"
                else:
                    payload["sequence"] = "N/A"
                label = "malformed"
                self.injected["malformed"] += 1

            latency = max(0.0, rng.gauss(cfg.mean_latency_s, cfg.latency_jitter_s))
            receive_ts = exchange_ts + latency
            raw = RawEvent(
                source=source,
                payload=payload,
                receive_timestamp=receive_ts,
                raw_id=f"sim-{next(_id_counter)}",
            )
            yield raw, label
            emitted += 1

            # -- Duplicate: queue an exact repeat to be emitted next.
            if rng.random() < cfg.duplicate_rate:
                dup_raw = RawEvent(
                    source=source,
                    payload=dict(payload),
                    receive_timestamp=receive_ts + 0.0001,
                    raw_id=f"sim-{next(_id_counter)}",
                )
                pending_duplicate = (dup_raw, "duplicate")
                self.injected["duplicate"] += 1
