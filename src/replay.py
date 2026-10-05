"""
MDRAP Deterministic Historical Replay Engine (Spec §23, §26).

Provides microsecond-paced deterministic market data playback across:
- SQLite persistent databases (canonical_events)
- Append-only binary journals (.dbn / AOF via BinaryJournalReader)
- Raw write-ahead archives (JSONL via RawArchive)
- In-memory event sequences

Guarantees tick-by-tick determinism, virtual clock synchronization,
and configurable speed multipliers (0.1x to 100x or max unthrottled).
"""

from __future__ import annotations

import contextlib
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Generator, Iterable, Optional

from models import CanonicalEvent, EventType, QualityStatus
from journal import BinaryJournalReader

__stability__ = "stable"


class VirtualClock:
    """
    Monotonically advancing virtual clock for deterministic replay.

    Provides an isolated time source that synchronizes with historical event timestamps
    rather than system wall-clock time, ensuring downstream components (watchdogs,
    staleness monitors, rate limiters) evaluate accurately during historical backtests.
    """

    def __init__(self, initial_time: float = 0.0):
        self._current_time: float = float(initial_time)
        self._is_running: bool = False
        self._start_wall_time: float = 0.0
        self._speed_factor: float = 1.0

    @property
    def now(self) -> float:
        """Current virtual timestamp in Unix seconds."""
        return self._current_time

    def set_time(self, timestamp: float) -> None:
        """Explicitly warp or advance virtual time to a timestamp."""
        self._current_time = float(timestamp)

    def advance(self, delta_seconds: float) -> float:
        """Advance virtual time monotonically by delta seconds."""
        if delta_seconds < 0:
            raise ValueError(
                f"Virtual clock cannot step backward: delta={delta_seconds}"
            )
        self._current_time += delta_seconds
        return self._current_time

    @contextlib.contextmanager
    def override_time(self):
        """
        Context manager that patches `time.time` to return virtual clock time.
        Restores the standard `time.time` on exit.
        """
        orig_time = time.time
        time.time = lambda: self._current_time
        try:
            yield self
        finally:
            time.time = orig_time


class ReplayPacer:
    """
    Microsecond-precision pacing controller for event playback.

    Supports configurable playback rates:
      - speed_factor = 1.0: Real-time wall-clock pacing
      - speed_factor > 1.0: Accelerated playback (e.g. 5x, 10x)
      - speed_factor < 1.0: Slow-motion playback (e.g. 0.1x)
      - speed_factor = None or 0.0: Max unthrottled speed (benchmark mode)
    """

    def __init__(self, speed_factor: Optional[float] = 1.0):
        self.speed_factor: Optional[float] = (
            speed_factor if speed_factor and speed_factor > 0 else None
        )
        self._prev_event_ts: Optional[float] = None
        self._prev_wall_perf: Optional[float] = None
        self._total_drift_us: float = 0.0
        self._pace_count: int = 0
        self._max_jitter_us: float = 0.0

    def reset(self) -> None:
        self._prev_event_ts = None
        self._prev_wall_perf = None
        self._total_drift_us = 0.0
        self._pace_count = 0
        self._max_jitter_us = 0.0

    def pace(self, current_event_ts: float) -> float:
        """
        Pace execution to maintain desired speed relative to event intervals.
        Returns the actual slept duration in seconds.
        """
        if self.speed_factor is None:
            # Unthrottled: do not delay
            return 0.0

        now_perf = time.perf_counter()
        if self._prev_event_ts is None or self._prev_wall_perf is None:
            self._prev_event_ts = current_event_ts
            self._prev_wall_perf = now_perf
            return 0.0

        virtual_delta = current_event_ts - self._prev_event_ts
        if virtual_delta <= 0:
            # Out-of-order or simultaneous events: no delay required
            self._prev_event_ts = current_event_ts
            return 0.0

        target_wall_delta = virtual_delta / self.speed_factor
        elapsed_wall = now_perf - self._prev_wall_perf
        sleep_needed = target_wall_delta - elapsed_wall

        actual_slept = 0.0
        if sleep_needed > 0:
            # Coarse sleep for intervals > 2ms
            if sleep_needed > 0.002:
                time.sleep(sleep_needed - 0.001)

            # High-precision spin loop for remainder to avoid OS scheduling quantization
            while (time.perf_counter() - now_perf) < sleep_needed:
                pass

            end_perf = time.perf_counter()
            actual_slept = end_perf - now_perf
            jitter_us = abs(actual_slept - sleep_needed) * 1_000_000.0
            self._max_jitter_us = max(self._max_jitter_us, jitter_us)
            self._total_drift_us += (actual_slept - sleep_needed) * 1_000_000.0
            self._pace_count += 1
            self._prev_wall_perf = end_perf
        else:
            self._prev_wall_perf = now_perf

        self._prev_event_ts = current_event_ts
        return actual_slept

    @property
    def avg_drift_us(self) -> float:
        return (
            (self._total_drift_us / self._pace_count) if self._pace_count > 0 else 0.0
        )

    @property
    def max_jitter_us(self) -> float:
        return self._max_jitter_us


@dataclass
class ReplayStats:
    """Operational telemetry for a replay execution."""

    events_replayed: int = 0
    total_events: int = 0
    start_event_ts: float = 0.0
    end_event_ts: float = 0.0
    wall_elapsed_s: float = 0.0
    virtual_elapsed_s: float = 0.0
    rate_eps: float = 0.0
    avg_drift_us: float = 0.0
    max_jitter_us: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "events_replayed": self.events_replayed,
            "total_events": self.total_events,
            "start_event_ts": self.start_event_ts,
            "end_event_ts": self.end_event_ts,
            "wall_elapsed_s": round(self.wall_elapsed_s, 4),
            "virtual_elapsed_s": round(self.virtual_elapsed_s, 4),
            "rate_eps": round(self.rate_eps, 2),
            "avg_drift_us": round(self.avg_drift_us, 2),
            "max_jitter_us": round(self.max_jitter_us, 2),
        }


class HistoricalReplayEngine:
    """
    Deterministic Historical Replay Engine.

    Reads market events from various persistence backends and yields/dispatches them
    in strict chronological order with virtual clock synchronization and precise pacing.
    """

    def __init__(
        self,
        speed_factor: Optional[float] = 1.0,
        sync_virtual_clock: bool = True,
    ):
        self.pacer = ReplayPacer(speed_factor=speed_factor)
        self.virtual_clock = VirtualClock()
        self.sync_virtual_clock = sync_virtual_clock
        self._is_paused: bool = False
        self._is_stopped: bool = False
        self._events_processed: int = 0

    def pause(self) -> None:
        self._is_paused = True

    def resume(self) -> None:
        self._is_paused = False

    def stop(self) -> None:
        self._is_stopped = True

    @staticmethod
    def _event_sort_key(event: CanonicalEvent) -> tuple:
        """Deterministic tie-breaking sorting key for market events."""
        return (
            event.exchange_timestamp,
            event.sequence_number if event.sequence_number is not None else -1,
            event.source or "",
            event.event_id or "",
        )

    def load_from_sqlite(
        self,
        db_path: str,
        symbol: Optional[str] = None,
        start_ts: Optional[float] = None,
        end_ts: Optional[float] = None,
        limit: Optional[int] = None,
    ) -> list[CanonicalEvent]:
        """Load and sort CanonicalEvents from an MDRAP SQLite database."""
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            query = "SELECT * FROM canonical_events WHERE 1=1"
            params: list[Any] = []
            if symbol:
                query += " AND instrument_id = ?"
                params.append(symbol)
            if start_ts is not None:
                query += " AND exchange_timestamp >= ?"
                params.append(start_ts)
            if end_ts is not None:
                query += " AND exchange_timestamp <= ?"
                params.append(end_ts)

            # Strict chronological order
            query += " ORDER BY exchange_timestamp ASC, sequence_number ASC, rowid ASC"
            if limit:
                query += f" LIMIT {int(limit)}"

            cursor = conn.execute(query, params)
            events: list[CanonicalEvent] = []
            for row in cursor.fetchall():
                reasons_val = row["reasons"]
                reasons_list = []
                if reasons_val:
                    try:
                        import json

                        reasons_list = (
                            json.loads(reasons_val)
                            if isinstance(reasons_val, str)
                            else list(reasons_val)
                        )
                    except Exception:
                        reasons_list = [str(reasons_val)]

                ev = CanonicalEvent(
                    event_id=row["event_id"],
                    instrument_id=row["instrument_id"],
                    event_type=EventType(row["event_type"])
                    if row["event_type"] in EventType.__members__
                    else EventType.TRADE,
                    exchange_timestamp=float(row["exchange_timestamp"] or 0.0),
                    receive_timestamp=float(row["receive_timestamp"] or 0.0),
                    processing_timestamp=float(row["processing_timestamp"] or 0.0),
                    source=row["source"] or "SQLITE",
                    sequence_number=row["sequence_number"],
                    price=float(row["price"]) if row["price"] is not None else None,
                    quantity=float(row["quantity"])
                    if row["quantity"] is not None
                    else None,
                    bid_price=float(row["bid_price"])
                    if row["bid_price"] is not None
                    else None,
                    bid_size=float(row["bid_size"])
                    if row["bid_size"] is not None
                    else None,
                    ask_price=float(row["ask_price"])
                    if row["ask_price"] is not None
                    else None,
                    ask_size=float(row["ask_size"])
                    if row["ask_size"] is not None
                    else None,
                    quality_status=QualityStatus(row["quality_status"])
                    if row["quality_status"] in QualityStatus.__members__
                    else QualityStatus.VALID,
                    reasons=reasons_list,
                    raw_id=row["raw_id"] or "",
                    source_kind="REPLAY",
                )
                events.append(ev)
            return events
        finally:
            conn.close()

    def load_from_journal(
        self,
        filepath: str,
        start_seq: int = 0,
        limit: Optional[int] = None,
    ) -> list[CanonicalEvent]:
        """Load CanonicalEvents from a binary journal (.dbn) file."""
        events: list[CanonicalEvent] = []
        with BinaryJournalReader(filepath) as reader:
            for idx, rec in enumerate(reader.scan_from_seq(start_seq)):
                if limit and idx >= limit:
                    break
                ev_type = EventType.DEPTH if rec["type"] == "DEPTH" else EventType.TRADE
                qs_str = rec.get("status", "VALID")
                qs = (
                    QualityStatus[qs_str]
                    if qs_str in QualityStatus.__members__
                    else QualityStatus.VALID
                )

                ev = CanonicalEvent(
                    event_id=f"jnl-{rec['seq']}",
                    instrument_id=rec["sym"],
                    event_type=ev_type,
                    exchange_timestamp=rec["exchange_ts"],
                    receive_timestamp=rec["ingest_ts"],
                    processing_timestamp=rec["broadcast_ts"],
                    source=rec.get("source", "JOURNAL"),
                    sequence_number=rec["seq"],
                    price=rec.get("price") or rec.get("micro_price"),
                    quantity=rec.get("size") or rec.get("ofi"),
                    bid_price=rec.get("bid"),
                    bid_size=rec.get("bid_size"),
                    ask_price=rec.get("ask"),
                    ask_size=rec.get("ask_size"),
                    quality_status=qs,
                    source_kind="REPLAY",
                )
                events.append(ev)
        return events

    def stream(
        self,
        events: Iterable[CanonicalEvent],
        pacing: bool = True,
    ) -> Generator[CanonicalEvent, None, ReplayStats]:
        """
        Stream events deterministically.
        Yields events one by one, respecting pacer constraints and updating virtual clock.
        """
        self.pacer.reset()
        self._is_stopped = False
        self._events_processed = 0

        # Sort input stream to guarantee deterministic execution
        event_list = list(events)
        event_list.sort(key=self._event_sort_key)

        total_count = len(event_list)
        if total_count == 0:
            return ReplayStats()

        start_event_ts = event_list[0].exchange_timestamp
        end_event_ts = event_list[-1].exchange_timestamp
        wall_start = time.perf_counter()

        if self.sync_virtual_clock:
            self.virtual_clock.set_time(start_event_ts)

        for event in event_list:
            if self._is_stopped:
                break

            while self._is_paused and not self._is_stopped:
                time.sleep(0.01)

            if pacing:
                self.pacer.pace(event.exchange_timestamp)

            if self.sync_virtual_clock:
                self.virtual_clock.set_time(event.exchange_timestamp)

            # Ensure event is explicitly tagged as historical replay provenance
            event.source_kind = "REPLAY"
            self._events_processed += 1
            yield event

        wall_elapsed = max(1e-6, time.perf_counter() - wall_start)
        virtual_elapsed = max(0.0, end_event_ts - start_event_ts)

        stats = ReplayStats(
            events_replayed=self._events_processed,
            total_events=total_count,
            start_event_ts=start_event_ts,
            end_event_ts=end_event_ts,
            wall_elapsed_s=wall_elapsed,
            virtual_elapsed_s=virtual_elapsed,
            rate_eps=self._events_processed / wall_elapsed,
            avg_drift_us=self.pacer.avg_drift_us,
            max_jitter_us=self.pacer.max_jitter_us,
        )
        return stats

    def replay_to_pipeline(
        self,
        pipeline: Any,
        events: Iterable[CanonicalEvent],
        pacing: bool = False,
    ) -> ReplayStats:
        """
        Replay historical events directly into an MDRAP Pipeline.
        """
        stats: Optional[ReplayStats] = None
        gen = self.stream(events, pacing=pacing)
        try:
            while True:
                ev = next(gen)
                # Pass into pipeline
                if hasattr(pipeline, "process_canonical"):
                    pipeline.process_canonical(ev)
                elif hasattr(pipeline, "process"):
                    pipeline.process(ev)
        except StopIteration as exc:
            stats = exc.value

        return stats or ReplayStats(events_replayed=self._events_processed)

    def replay_to_shm(
        self,
        shm_writer: Any,
        events: Iterable[CanonicalEvent],
        pacing: bool = False,
    ) -> ReplayStats:
        """
        Replay historical events into an SHM ring buffer writer.
        """
        stats: Optional[ReplayStats] = None
        gen = self.stream(events, pacing=pacing)
        try:
            while True:
                ev = next(gen)
                if hasattr(shm_writer, "publish_canonical"):
                    shm_writer.publish_canonical(ev)
                elif hasattr(shm_writer, "write_tick"):
                    shm_writer.write_tick(
                        symbol=ev.instrument_id,
                        price=ev.price,
                        size=ev.quantity,
                        bid=ev.bid_price,
                        ask=ev.ask_price,
                        exchange_ts=ev.exchange_timestamp,
                        source=ev.source,
                    )
        except StopIteration as exc:
            stats = exc.value

        return stats or ReplayStats(events_replayed=self._events_processed)
