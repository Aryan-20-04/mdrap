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
    ):
        self.reliability = reliability
        self.silence_threshold_s = silence_threshold_s
        self.degradation_threshold = degradation_threshold
        self.recovery_threshold = recovery_threshold

        self._source_states: dict[str, SourceState] = {}
        self._last_seen: dict[str, float] = {}
        self._alerts: list[WatchdogAlert] = []
        self._blocked_sources: set[str] = set()

    def observe(self, event: CanonicalEvent) -> list[WatchdogAlert]:
        """
        Update watchdog state with a new event and check for failures.
        Returns a list of alerts if state changes occur.
        """
        self._last_seen[event.source] = event.exchange_timestamp
        if event.source not in self._source_states:
            self._source_states[event.source] = SourceState.HEALTHY

        current_time = event.exchange_timestamp
        new_alerts = []

        # Pull cached scores from the reliability tracker
        scores = getattr(self.reliability, "_cached_scores", {})

        for source, state in self._source_states.items():
            if state == SourceState.BLOCKED:
                continue

            last_seen_time = self._last_seen.get(source, 0.0)
            score = scores.get(source, 1.0)

            is_silent = (current_time - last_seen_time) > self.silence_threshold_s
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
