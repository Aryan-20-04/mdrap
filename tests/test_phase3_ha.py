"""Tests for Phase 3 Workstream I — High Availability, Replication, and Sequence Continuity."""

import time
import pytest
from mdrap.failover import (
    FailoverNode,
    HeartbeatMessage,
    NodeState,
    StaleEpochError,
)


def test_heartbeat_exchange():
    """Standby node successfully tracks primary heartbeats and sequence watermarks."""
    primary = FailoverNode("node-1", initial_state=NodeState.PRIMARY, initial_epoch=1)
    standby = FailoverNode("node-2", initial_state=NodeState.STANDBY, initial_epoch=1)

    hb = primary.send_heartbeat(last_committed_seq=5000, health_score=1.0)
    assert hb.epoch == 1
    assert hb.state == NodeState.PRIMARY

    accepted = standby.receive_heartbeat(hb)
    assert accepted is True
    assert standby._primary_node_id == "node-1"
    assert standby._primary_last_seq == 5000


def test_failover_on_primary_silence():
    """Standby promotes itself to PRIMARY when primary heartbeats exceed timeout."""
    standby = FailoverNode(
        "node-2",
        initial_state=NodeState.STANDBY,
        heartbeat_timeout_s=0.05,
        initial_epoch=1,
    )

    # Receive initial primary heartbeat
    hb = HeartbeatMessage(
        node_id="node-1",
        cluster_id="mdrap-cluster",
        epoch=1,
        state=NodeState.PRIMARY,
        last_committed_seq=100,
        timestamp=time.time() - 0.1,  # In the past
        fencing_token=1,
    )
    standby.receive_heartbeat(hb)

    # Standby checks failover condition with local sequence caught up (seq=100)
    promoted = standby.check_failover_condition(local_seq=100)
    assert promoted is True
    assert standby.state == NodeState.PRIMARY
    assert standby.epoch == 2
    assert standby.fencing_token == 2


def test_stale_fencing_token_rejection():
    """Fencing token validation prevents stale writers from publishing."""
    node = FailoverNode("node-1", initial_state=NodeState.PRIMARY, initial_epoch=5)

    # Valid token passes
    node.assert_fencing_token(5)

    # Stale token raises StaleEpochError
    with pytest.raises(StaleEpochError, match="Fencing token 4 is stale"):
        node.assert_fencing_token(4)

    # Demoted node raises StaleEpochError on any write attempt
    node.demote()
    with pytest.raises(StaleEpochError, match="cannot write as PRIMARY"):
        node.assert_fencing_token(5)


def test_split_brain_resolution():
    """Concurrent primaries at same epoch resolve deterministically via node_id tie-breaker."""
    node_a = FailoverNode("node-A", initial_state=NodeState.PRIMARY, initial_epoch=1)
    node_b = FailoverNode("node-B", initial_state=NodeState.PRIMARY, initial_epoch=1)

    hb_b = node_b.send_heartbeat()
    node_a.receive_heartbeat(hb_b)
    assert node_a.state == NodeState.PRIMARY  # "node-A" < "node-B", retains PRIMARY

    hb_a = node_a.send_heartbeat()
    node_b.receive_heartbeat(hb_a)
    assert node_b.state == NodeState.STANDBY  # "node-B" > "node-A", yields to STANDBY


def test_standby_catchup_syncing_state():
    """Standby does not prematurely promote if sequence is behind primary."""
    standby = FailoverNode(
        "node-2",
        initial_state=NodeState.STANDBY,
        heartbeat_timeout_s=0.01,
        initial_epoch=1,
    )
    hb = HeartbeatMessage(
        node_id="node-1",
        cluster_id="mdrap-cluster",
        epoch=1,
        state=NodeState.PRIMARY,
        last_committed_seq=1000,
        timestamp=time.time() - 0.05,
        fencing_token=1,
    )
    standby.receive_heartbeat(hb)

    # Local seq is only 500 (behind 1000)
    promoted = standby.check_failover_condition(local_seq=500)
    assert promoted is False
    assert standby.state == NodeState.SYNCING

    # Standby catches up
    standby.replicate_commit(seq=1000)
    assert standby.state == NodeState.STANDBY

    # Now promotion succeeds
    promoted_now = standby.check_failover_condition(local_seq=1000)
    assert promoted_now is True
    assert standby.state == NodeState.PRIMARY
