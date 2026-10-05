"""
Tests for Active-Passive Failover State Machine and Disaster Recovery (src/failover.py).
"""

import time
import pytest
from failover import (
    FailoverNode,
    HeartbeatMessage,
    NodeState,
)


def test_failover_node_initialization_and_heartbeat():
    """Verify FailoverNode starts in STANDBY and emits well-formed HeartbeatMessage."""
    node = FailoverNode("node-1", cluster_id="prod-cluster", initial_state=NodeState.STANDBY)
    assert node.state == NodeState.STANDBY
    assert node.epoch == 1
    assert node.fencing_token == 1

    hb = node.send_heartbeat(last_committed_seq=1050, health_score=0.98)
    assert hb.node_id == "node-1"
    assert hb.cluster_id == "prod-cluster"
    assert hb.state == NodeState.STANDBY
    assert hb.last_committed_seq == 1050
    assert hb.health_score == 0.98


def test_standby_tracks_primary_heartbeat():
    """Verify standby node records primary liveness and sequence progression."""
    standby = FailoverNode("node-2", cluster_id="c1", initial_state=NodeState.STANDBY)

    primary_hb = HeartbeatMessage(
        node_id="node-1",
        cluster_id="c1",
        epoch=1,
        state=NodeState.PRIMARY,
        last_committed_seq=5000,
        timestamp=time.time(),
        health_score=1.0,
        fencing_token=1,
    )

    accepted = standby.receive_heartbeat(primary_hb)
    assert accepted is True
    assert standby._primary_node_id == "node-1"
    assert standby._primary_last_seq == 5000

    stats = standby.stats()
    assert stats["last_primary_node_id"] == "node-1"
    assert stats["last_primary_age_s"] is not None
    assert stats["last_primary_age_s"] < 0.5


def test_failover_timeout_triggers_promotion():
    """Verify standby node promotes to PRIMARY when primary heartbeat times out."""
    standby = FailoverNode(
        "node-2",
        cluster_id="c1",
        initial_state=NodeState.STANDBY,
        heartbeat_timeout_s=0.1,  # Short timeout for fast testing
    )

    # Receive an initial heartbeat with timestamp 0.2s in the past
    past_hb = HeartbeatMessage(
        node_id="node-1",
        cluster_id="c1",
        epoch=1,
        state=NodeState.PRIMARY,
        last_committed_seq=100,
        timestamp=time.time() - 0.25,
        health_score=1.0,
        fencing_token=1,
    )
    standby.receive_heartbeat(past_hb)

    # Local node sequence is 100 (caught up)
    promoted = standby.check_failover_condition(local_seq=100)
    assert promoted is True
    assert standby.state == NodeState.PRIMARY
    assert standby.epoch == 2  # Incremented
    assert standby.fencing_token == 2
    assert standby.stats()["failover_count"] == 1


def test_failover_sequence_lag_enters_syncing_state():
    """Verify standby node enters SYNCING if local sequence is behind primary when timeout occurs."""
    standby = FailoverNode(
        "node-2",
        cluster_id="c1",
        initial_state=NodeState.STANDBY,
        heartbeat_timeout_s=0.1,
    )

    past_hb = HeartbeatMessage(
        node_id="node-1",
        cluster_id="c1",
        epoch=1,
        state=NodeState.PRIMARY,
        last_committed_seq=500,
        timestamp=time.time() - 0.25,
        health_score=1.0,
        fencing_token=1,
    )
    standby.receive_heartbeat(past_hb)

    # Local sequence is only 400 (behind by 100)
    promoted = standby.check_failover_condition(local_seq=400)
    assert promoted is False
    assert standby.state == NodeState.SYNCING

    # Once local sequence catches up, promote directly
    standby.state = NodeState.STANDBY
    promoted_after_catchup = standby.check_failover_condition(local_seq=500)
    assert promoted_after_catchup is True
    assert standby.state == NodeState.PRIMARY


def test_split_brain_resolution_deterministic_tie_breaker():
    """Verify split-brain resolution when two nodes assert PRIMARY in same epoch."""
    # Node A ("node-alpha") and Node B ("node-beta")
    node_alpha = FailoverNode("node-alpha", cluster_id="c1", initial_state=NodeState.PRIMARY, initial_epoch=1)
    node_beta = FailoverNode("node-beta", cluster_id="c1", initial_state=NodeState.PRIMARY, initial_epoch=1)

    # Node Beta receives heartbeat from Node Alpha
    hb_alpha = node_alpha.send_heartbeat(last_committed_seq=1000)
    node_beta.receive_heartbeat(hb_alpha)

    # "node-beta" > "node-alpha" lexicographically, so node_beta yields to STANDBY
    assert node_beta.state == NodeState.STANDBY
    assert node_alpha.state == NodeState.PRIMARY


def test_epoch_fencing_stale_primary_rejection():
    """Verify a node rejecting a heartbeat from a stale primary with lower epoch."""
    active_primary = FailoverNode("node-2", cluster_id="c1", initial_state=NodeState.PRIMARY, initial_epoch=3)

    # Zombie former primary sends heartbeat with epoch=1
    stale_hb = HeartbeatMessage(
        node_id="node-1",
        cluster_id="c1",
        epoch=1,
        state=NodeState.PRIMARY,
        last_committed_seq=999,
        timestamp=time.time(),
        health_score=1.0,
        fencing_token=1,
    )

    accepted = active_primary.receive_heartbeat(stale_hb)
    assert accepted is False
    assert active_primary.state == NodeState.PRIMARY
    assert active_primary.epoch == 3
