"""
MDRAP Active-Passive Failover State Machine & High Availability Engine (Spec §25, §26).

Provides institutional disaster recovery and high availability with:
- Deterministic finite state machine: PRIMARY, STANDBY, SYNCING, FAILED, MAINTENANCE
- Epoch-based fencing tokens preventing split-brain conditions
- Heartbeat tracking with sub-second failover detection
- Catch-up sequence parity verification prior to promotion
- Graceful voluntary handoff and emergency failover protocols
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

__stability__ = "stable"


class NodeState(str, enum.Enum):
    PRIMARY = "PRIMARY"
    STANDBY = "STANDBY"
    SYNCING = "SYNCING"
    FAILED = "FAILED"
    MAINTENANCE = "MAINTENANCE"

    def __str__(self) -> str:
        return self.value


class StaleEpochError(RuntimeError):
    """Raised when an operation is attempted with an expired fencing token or epoch."""

    pass


class SplitBrainDetected(RuntimeError):
    """Raised when concurrent nodes assert primary status within the same cluster."""

    pass


@dataclass(slots=True)
class HeartbeatMessage:
    """Cluster node heartbeat and state telemetry."""

    node_id: str
    cluster_id: str
    epoch: int
    state: NodeState
    last_committed_seq: int
    timestamp: float
    health_score: float = 1.0  # 0.0 (unhealthy) to 1.0 (optimal)
    fencing_token: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "cluster_id": self.cluster_id,
            "epoch": self.epoch,
            "state": self.state.value
            if hasattr(self.state, "value")
            else str(self.state),
            "last_committed_seq": self.last_committed_seq,
            "timestamp": self.timestamp,
            "health_score": round(self.health_score, 4),
            "fencing_token": self.fencing_token,
        }


class FailoverNode:
    """
    Active-Passive cluster node state coordinator.
    Enforces epoch fencing, monitors peer liveness, and coordinates seamless promotion/demotion.
    """

    def __init__(
        self,
        node_id: str,
        cluster_id: str = "mdrap-cluster",
        initial_state: NodeState = NodeState.STANDBY,
        heartbeat_timeout_s: float = 2.0,
        initial_epoch: int = 1,
    ):
        self.node_id: str = str(node_id)
        self.cluster_id: str = str(cluster_id)
        self.state: NodeState = initial_state
        self.heartbeat_timeout_s: float = float(heartbeat_timeout_s)
        self.epoch: int = int(initial_epoch)
        self.fencing_token: int = int(initial_epoch)

        # Peer tracking
        self._last_primary_hb_time: float = 0.0
        self._primary_node_id: Optional[str] = None
        self._primary_last_seq: int = 0
        self._primary_epoch: int = 0

        # Telemetry
        self._heartbeats_sent: int = 0
        self._heartbeats_received: int = 0
        self._failover_count: int = 0
        self._last_state_change: float = time.time()
        self._on_state_change_callback: Optional[
            Callable[[NodeState, NodeState], None]
        ] = None

    def set_state_change_callback(
        self, cb: Callable[[NodeState, NodeState], None]
    ) -> None:
        self._on_state_change_callback = cb

    def _transition_to(self, new_state: NodeState, reason: str = "") -> None:
        old_state = self.state
        if old_state == new_state:
            return
        self.state = new_state
        self._last_state_change = time.time()
        if self._on_state_change_callback:
            try:
                self._on_state_change_callback(old_state, new_state)
            except Exception:
                pass

    def send_heartbeat(
        self,
        last_committed_seq: int = 0,
        health_score: float = 1.0,
    ) -> HeartbeatMessage:
        """Construct and broadcast an outbound heartbeat message."""
        self._heartbeats_sent += 1
        return HeartbeatMessage(
            node_id=self.node_id,
            cluster_id=self.cluster_id,
            epoch=self.epoch,
            state=self.state,
            last_committed_seq=last_committed_seq,
            timestamp=time.time(),
            health_score=max(0.0, min(1.0, health_score)),
            fencing_token=self.fencing_token,
        )

    def receive_heartbeat(self, hb: HeartbeatMessage) -> bool:
        """
        Process an inbound peer heartbeat.
        Enforces fencing tokens, detects split-brain assertions, and tracks primary liveness.
        """
        if hb.cluster_id != self.cluster_id:
            # Different cluster, ignore
            return False

        if hb.node_id == self.node_id:
            # Loopback heartbeat, ignore
            return True

        self._heartbeats_received += 1

        # Epoch fencing: if peer has higher epoch, adopt it
        if hb.epoch > self.epoch:
            self.epoch = hb.epoch
            self.fencing_token = hb.fencing_token
            if self.state == NodeState.PRIMARY and hb.state == NodeState.PRIMARY:
                # Demote self: higher epoch wins
                self._transition_to(
                    NodeState.STANDBY, reason="Higher epoch primary discovered"
                )
                return True

        # Stale heartbeat from old epoch
        if hb.epoch < self.epoch and hb.state == NodeState.PRIMARY:
            # Disregard stale primary assertion
            return False

        # Split-brain resolution: both assert PRIMARY at same epoch
        if (
            self.state == NodeState.PRIMARY
            and hb.state == NodeState.PRIMARY
            and hb.epoch == self.epoch
        ):
            # Deterministic tie-breaker:
            # Node with lower node_id remains PRIMARY; higher node_id yields to STANDBY
            if self.node_id > hb.node_id:
                self._transition_to(
                    NodeState.STANDBY, reason="Split-brain tie-breaker lost"
                )
                self._last_primary_hb_time = hb.timestamp
                self._primary_node_id = hb.node_id
                self._primary_last_seq = hb.last_committed_seq
                self._primary_epoch = hb.epoch
                return True
            else:
                # We retain PRIMARY; peer should yield
                return False

        # Track active primary
        if hb.state == NodeState.PRIMARY:
            self._last_primary_hb_time = hb.timestamp
            self._primary_node_id = hb.node_id
            self._primary_last_seq = hb.last_committed_seq
            self._primary_epoch = hb.epoch

        return True

    def check_failover_condition(self, local_seq: int = 0) -> bool:
        """
        Check if primary node has gone silent past heartbeat timeout.
        If timeout is exceeded, promotes this standby node to PRIMARY.
        """
        if self.state != NodeState.STANDBY:
            return False

        # If we have never received a primary heartbeat, don't auto-promote unless explicit
        if self._last_primary_hb_time == 0.0:
            return False

        age = time.time() - self._last_primary_hb_time
        if age <= self.heartbeat_timeout_s:
            return False

        # Primary timeout detected! Check sequence catch-up
        if local_seq < self._primary_last_seq:
            # Local node is behind, enter SYNCING state to drain backlog
            self._transition_to(
                NodeState.SYNCING, reason="Catching up to last primary sequence"
            )
            return False

        # Sequence is caught up (or equal/greater). Promote to PRIMARY
        self.promote(
            reason=f"Primary '{self._primary_node_id}' timed out after {age:.2f}s"
        )
        return True

    def promote(self, reason: str = "manual") -> None:
        """
        Promote node to PRIMARY role. Increments cluster epoch and fencing token.
        """
        self.epoch += 1
        self.fencing_token = self.epoch
        self._failover_count += 1
        self._transition_to(NodeState.PRIMARY, reason=reason)

    def demote(self, reason: str = "manual") -> None:
        """Gracefully transition to STANDBY role."""
        self._transition_to(NodeState.STANDBY, reason=reason)

    def enter_maintenance(self) -> None:
        """Take node offline for maintenance."""
        self._transition_to(NodeState.MAINTENANCE, reason="Operator maintenance")

    def fail(self, reason: str = "unrecoverable error") -> None:
        """Mark node as FAILED."""
        self._transition_to(NodeState.FAILED, reason=reason)

    def stats(self) -> dict[str, Any]:
        age_primary = (
            (time.time() - self._last_primary_hb_time)
            if self._last_primary_hb_time > 0
            else None
        )
        return {
            "node_id": self.node_id,
            "cluster_id": self.cluster_id,
            "state": self.state.value
            if hasattr(self.state, "value")
            else str(self.state),
            "epoch": self.epoch,
            "fencing_token": self.fencing_token,
            "heartbeats_sent": self._heartbeats_sent,
            "heartbeats_received": self._heartbeats_received,
            "failover_count": self._failover_count,
            "last_primary_node_id": self._primary_node_id,
            "last_primary_age_s": round(age_primary, 3)
            if age_primary is not None
            else None,
            "state_duration_s": round(time.time() - self._last_state_change, 2),
        }
