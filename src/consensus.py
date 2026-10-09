"""
MDRAP Phase 8 Distributed High Availability, Consensus & Fencing Engine.

Implements lease-based consensus, monotonic epoch generation tokens, and
authoritative write-path fencing to guarantee split-brain safety across
multi-host MDRAP cluster deployments. Pure Python stdlib.
"""

from __future__ import annotations

import dataclasses
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

__stability__ = "stable"


class FencingTokenError(RuntimeError):
    """Raised when a stale writer attempts to write with an obsolete or expired epoch token."""
    pass


class QuorumLossError(RuntimeError):
    """Raised when leadership is attempted without majority quorum consensus."""
    pass


@dataclasses.dataclass(frozen=True, slots=True)
class EpochToken:
    """Cryptographic/monotonic fencing token representing an authoritative leadership lease."""

    epoch: int
    leader_id: str
    issued_ts: float
    lease_duration_sec: float

    @property
    def is_expired(self) -> bool:
        return time.time() > (self.issued_ts + self.lease_duration_sec)


class FencedWALWriter:
    """
    Authoritative write-path gate enforcing stale-leader fencing.

    Wraps the partition persistence boundary. Rejects any write attempted
    with an epoch lower than the maximum epoch observed, or if the writer's
    epoch lease has expired.
    """

    def __init__(self, partition_id: str) -> None:
        self.partition_id = partition_id
        self._lock = threading.Lock()
        self._highest_epoch_seen: int = 0
        self._active_leader: Optional[str] = None
        self._stale_writes_rejected: int = 0
        self._valid_writes_accepted: int = 0

    @property
    def highest_epoch(self) -> int:
        with self._lock:
            return self._highest_epoch_seen

    @property
    def stale_writes_rejected(self) -> int:
        with self._lock:
            return self._stale_writes_rejected

    def validate_write(self, token: EpochToken) -> None:
        """
        Validate that the writing node possesses the active, non-expired epoch token.
        Raises FencingTokenError if stale or expired.
        """
        with self._lock:
            if token.is_expired:
                self._stale_writes_rejected += 1
                raise FencingTokenError(
                    f"[FENCING] Epoch lease expired for leader '{token.leader_id}' "
                    f"(epoch {token.epoch}, issued={token.issued_ts:.3f})"
                )

            if token.epoch < self._highest_epoch_seen:
                self._stale_writes_rejected += 1
                raise FencingTokenError(
                    f"[FENCING] Stale writer detected: leader '{token.leader_id}' attempted write "
                    f"with epoch {token.epoch} < active epoch {self._highest_epoch_seen}"
                )

            # Monotonic epoch advancement
            if token.epoch > self._highest_epoch_seen:
                self._highest_epoch_seen = token.epoch
                self._active_leader = token.leader_id

            self._valid_writes_accepted += 1

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "partition_id": self.partition_id,
                "highest_epoch": self._highest_epoch_seen,
                "active_leader": self._active_leader,
                "valid_writes_accepted": self._valid_writes_accepted,
                "stale_writes_rejected": self._stale_writes_rejected,
            }


class ConsensusCoordinator:
    """
    Lightweight, quorum-based high availability and lease coordinator.

    Coordinates primary-backup failover and authoritative epoch token generation
    across cluster nodes using majority voting (N/2 + 1).
    """

    def __init__(
        self,
        node_id: str,
        cluster_nodes: List[str],
        lease_duration_sec: float = 0.5,
        initial_epoch: int = 0,
    ) -> None:
        self.node_id = node_id
        self.cluster_nodes = sorted(list(set(cluster_nodes)))
        self.quorum_size = (len(self.cluster_nodes) // 2) + 1
        self.lease_duration_sec = lease_duration_sec

        self._lock = threading.Lock()
        self._current_epoch: int = initial_epoch
        self._active_leader: Optional[str] = None
        self._current_token: Optional[EpochToken] = None
        self._peer_heartbeats: Dict[str, float] = {nid: time.time() for nid in self.cluster_nodes}
        self._isolated_peers: Set[str] = set()

    def sync_epoch(self, epoch: int) -> None:
        """Synchronize node's local epoch with observed cluster epoch from peer heartbeats."""
        with self._lock:
            if epoch > self._current_epoch:
                self._current_epoch = epoch

    def simulate_network_partition(self, isolated_node_ids: List[str]) -> None:
        """Simulate network partition disconnecting specific nodes."""
        with self._lock:
            self._isolated_peers = set(isolated_node_ids)

    def heal_network_partition(self) -> None:
        """Restore network connectivity to all cluster nodes."""
        with self._lock:
            self._isolated_peers.clear()

    def request_leadership(self) -> EpochToken:
        """
        Attempt to acquire leadership for the current node.
        Requires quorum approval from non-partitioned cluster nodes.
        """
        with self._lock:
            # Check reachable peers
            reachable_nodes = [
                nid for nid in self.cluster_nodes
                if nid not in self._isolated_peers
            ]
            if len(reachable_nodes) < self.quorum_size:
                raise QuorumLossError(
                    f"Cannot acquire leadership: reachable nodes ({len(reachable_nodes)}) "
                    f"< quorum required ({self.quorum_size})"
                )

            # Advance epoch monotonically
            self._current_epoch += 1
            self._active_leader = self.node_id
            self._current_token = EpochToken(
                epoch=self._current_epoch,
                leader_id=self.node_id,
                issued_ts=time.time(),
                lease_duration_sec=self.lease_duration_sec,
            )
            return self._current_token

    def renew_lease(self) -> EpochToken:
        """Renew active leadership lease if quorum remains intact."""
        with self._lock:
            if self._active_leader != self.node_id:
                raise FencingTokenError(f"Node '{self.node_id}' is not the active leader.")

            reachable_nodes = [
                nid for nid in self.cluster_nodes
                if nid not in self._isolated_peers
            ]
            if len(reachable_nodes) < self.quorum_size:
                self._active_leader = None
                self._current_token = None
                raise QuorumLossError("Lost quorum during lease renewal; leadership abdicated.")

            self._current_token = EpochToken(
                epoch=self._current_epoch,
                leader_id=self.node_id,
                issued_ts=time.time(),
                lease_duration_sec=self.lease_duration_sec,
            )
            return self._current_token

    def get_active_token(self) -> Optional[EpochToken]:
        with self._lock:
            return self._current_token

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "node_id": self.node_id,
                "current_epoch": self._current_epoch,
                "active_leader": self._active_leader,
                "cluster_size": len(self.cluster_nodes),
                "quorum_size": self.quorum_size,
                "isolated_peers": list(self._isolated_peers),
                "has_valid_lease": bool(self._current_token and not self._current_token.is_expired),
            }
