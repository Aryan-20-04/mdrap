"""
Live Watchdog & Automated Source Failover Module for MDRAP Phase 7.

Implements Spec §6.13 — Live watchdog and automated source failover.
Monitors source health in real-time and triggers automatic failover when sources degrade or go silent.
The watchdog is driven synchronously by observe() calls in the pipeline event loop.
"""

from dataclasses import dataclass
from enum import Enum
import time

from models import CanonicalEvent
from reconciliation import ReliabilityTracker

__stability__ = "stable"


@dataclass
class WatchdogAlert:
    source: str
    alert_type: str  # 'SILENCE', 'DEGRADATION', 'RECOVERY', 'BLOCKED', 'UNBLOCKED'
    timestamp: float
    details: str
    action_taken: str


class SourceState(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    SILENT = "SILENT"
    BLOCKED = "BLOCKED"


class SourceWatchdog:
    def __init__(
        self,
        reliability: ReliabilityTracker,
        silence_threshold_s: float = 2.0,
        degradation_threshold: float = 0.90,
        recovery_threshold: float = 0.93,
        min_ticks_for_adaptive: int = 20,
        adaptive_multiplier: float = 10.0,
    ):
        self.reliability = reliability
        self.silence_threshold_s = silence_threshold_s
        self.degradation_threshold = degradation_threshold
        self.recovery_threshold = recovery_threshold
        self.min_ticks_for_adaptive = min_ticks_for_adaptive
        self.adaptive_multiplier = adaptive_multiplier

        self._source_states: dict[str, SourceState] = {}
        self._last_seen: dict[str, float] = {}
        self._ewma_interval: dict[str, float] = {}
        self._tick_counts: dict[str, int] = {}
        self.adaptive_silence_count: int = 0
        self._alerts: list[WatchdogAlert] = []
        self._blocked_sources: set[str] = set()

    def observe(self, event: CanonicalEvent) -> list[WatchdogAlert]:
        """
        Update watchdog state with a new event and check for failures.
        Returns a list of alerts if state changes occur.
        """
        source_in = event.source
        now_ts = event.exchange_timestamp
        prev_ts = self._last_seen.get(source_in)
        if prev_ts is not None and now_ts > prev_ts:
            gap = now_ts - prev_ts
            ewma = self._ewma_interval.get(source_in, gap)
            self._ewma_interval[source_in] = 0.9 * ewma + 0.1 * gap
            self._tick_counts[source_in] = self._tick_counts.get(source_in, 0) + 1
        elif source_in not in self._tick_counts:
            self._tick_counts[source_in] = 1

        self._last_seen[event.source] = event.exchange_timestamp
        if event.source not in self._source_states:
            self._source_states[event.source] = SourceState.HEALTHY

        current_time = event.exchange_timestamp
        new_alerts = []

        # Pull cached scores from the reliability tracker
        scores = getattr(self.reliability, "_cached_scores", {})

        for source, state in list(self._source_states.items()):
            if state == SourceState.BLOCKED:
                continue

            last_seen_time = self._last_seen.get(source, 0.0)
            score = scores.get(source, 1.0)

            fixed_silent = (current_time - last_seen_time) > self.silence_threshold_s

            adaptive_silent = False
            typical_gap = self._ewma_interval.get(source, self.silence_threshold_s)
            if self._tick_counts.get(source, 0) >= self.min_ticks_for_adaptive:
                # Sibling feed is active if it has sent recently within its normal pace
                # and didn't itself just wake up from an extended gap
                others_active = any(
                    s != source
                    and (current_time - self._last_seen.get(s, 0.0))
                    <= (
                        self._ewma_interval.get(s, self.silence_threshold_s)
                        * (self.adaptive_multiplier / 2.0)
                    )
                    and (
                        event.source != s
                        or (
                            prev_ts is not None
                            and (now_ts - prev_ts) <= typical_gap * 3.0
                        )
                    )
                    for s in list(self._source_states.keys())
                )
                if others_active and (current_time - last_seen_time) > (
                    typical_gap * self.adaptive_multiplier
                ):
                    adaptive_silent = True

            is_silent = fixed_silent or adaptive_silent
            is_degraded = score < self.degradation_threshold
            is_recovered_score = score >= self.recovery_threshold

            new_state = state

            if is_silent:
                new_state = SourceState.SILENT
            elif is_degraded:
                new_state = SourceState.DEGRADED
            elif state in (SourceState.SILENT, SourceState.DEGRADED):
                if not is_silent and is_recovered_score:
                    new_state = SourceState.HEALTHY

            if new_state != state:
                alert_type = ""
                details = ""
                action_taken = ""

                if new_state == SourceState.SILENT:
                    alert_type = "SILENCE"
                    if adaptive_silent and not fixed_silent:
                        self.adaptive_silence_count += 1
                        details = (
                            f"Source {source} adaptive silence: gap {current_time - last_seen_time:.4f}s > "
                            f"{self.adaptive_multiplier}x typical ({typical_gap:.4f}s) while peer feeds active"
                        )
                        action_taken = (
                            "Marked SILENT (adaptive), initiating fast failover"
                        )
                    else:
                        details = (
                            f"Source {source} silent for > {self.silence_threshold_s}s"
                        )
                        action_taken = "Marked SILENT, initiating failover"
                elif new_state == SourceState.DEGRADED:
                    alert_type = "DEGRADATION"
                    details = f"Source {source} reliability score {score:.3f} below threshold {self.degradation_threshold}"
                    action_taken = "Marked DEGRADED, routing away"
                elif new_state == SourceState.HEALTHY:
                    alert_type = "RECOVERY"
                    details = f"Source {source} recovered (score {score:.3f}, active)"
                    action_taken = "Marked HEALTHY, resuming routing"

                self._source_states[source] = new_state
                alert = WatchdogAlert(
                    source=source,
                    alert_type=alert_type,
                    timestamp=current_time,
                    details=details,
                    action_taken=action_taken,
                )
                self._alerts.append(alert)
                new_alerts.append(alert)

        return new_alerts

    def block_source(self, source: str) -> WatchdogAlert:
        """Manually block a source."""
        self._blocked_sources.add(source)
        self._source_states[source] = SourceState.BLOCKED
        alert = WatchdogAlert(
            source=source,
            alert_type="BLOCKED",
            timestamp=time.time(),
            details=f"Source {source} manually blocked",
            action_taken="Marked BLOCKED",
        )
        self._alerts.append(alert)
        return alert

    def unblock_source(self, source: str) -> WatchdogAlert:
        """Remove manual block from a source."""
        if source in self._blocked_sources:
            self._blocked_sources.remove(source)
        self._source_states[source] = SourceState.HEALTHY
        alert = WatchdogAlert(
            source=source,
            alert_type="UNBLOCKED",
            timestamp=time.time(),
            details=f"Source {source} manually unblocked",
            action_taken="Marked HEALTHY",
        )
        self._alerts.append(alert)
        return alert

    def observe_shm_watermark(
        self, shm_obj: object, source_name: str = "SHM"
    ) -> WatchdogAlert | None:
        """Check if SHM watermark warning is active and issue alert."""
        is_warn = getattr(shm_obj, "is_watermark_warning_set", None)
        if is_warn and is_warn():
            alert = WatchdogAlert(
                source=source_name,
                alert_type="WATERMARK_WARNING",
                timestamp=time.time(),
                details=f"SHM ring buffer watermark crossed threshold on {source_name}",
                action_taken="Signaled backpressure / telemetry alert",
            )
            self._alerts.append(alert)
            return alert
        return None

    def source_states(self) -> dict[str, str]:
        """Return the current state of all known sources."""
        return {src: state.value for src, state in self._source_states.items()}

    def alerts(self, limit: int = 50) -> list[WatchdogAlert]:
        """Return the most recent N alerts."""
        return self._alerts[-limit:]

    def active_sources(self) -> list[str]:
        """Return list of sources that are HEALTHY."""
        return [
            src
            for src, state in self._source_states.items()
            if state == SourceState.HEALTHY
        ]

    def is_source_active(self, source: str) -> bool:
        """Check if a specific source is HEALTHY."""
        return self._source_states.get(source) == SourceState.HEALTHY
