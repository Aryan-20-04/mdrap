"""
Unit & Integration Tests for MDRAP Phase 8 High Availability, Consensus & Fencing Engine.

Verifies:
1. Quorum-based leader election and monotonic epoch advancement.
2. Authoritative WAL write boundary fencing (INV-08, INV-13).
3. Stale leader fencing: old leader write rejection after failover.
4. Lease expiration rejection: expired epoch token cannot write to WAL.
5. Network partition & quorum loss safety (split-brain prevention).
6. Simulated failover recovery under active workloads.
"""

import sys
import os
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mdrap.consensus import (
    ConsensusCoordinator,
    FencedWALWriter,
    EpochToken,
    FencingTokenError,
    QuorumLossError,
)


def test_quorum_election_and_fenced_write():
    nodes = ["node_1", "node_2", "node_3"]
    coord = ConsensusCoordinator("node_1", nodes, lease_duration_sec=1.0)
    writer = FencedWALWriter("partition_US_EQUITIES")

    # Acquire initial leadership
    token = coord.request_leadership()
    assert token.epoch == 1
    assert token.leader_id == "node_1"
    assert not token.is_expired

    # Valid write accepted
    writer.validate_write(token)
    assert writer.highest_epoch == 1
    assert writer.stale_writes_rejected == 0


def test_stale_leader_fencing_after_failover():
    nodes = ["node_1", "node_2", "node_3"]
    coord_1 = ConsensusCoordinator("node_1", nodes, lease_duration_sec=1.0)
    coord_2 = ConsensusCoordinator("node_2", nodes, lease_duration_sec=1.0)
    writer = FencedWALWriter("partition_US_EQUITIES")

    # Node 1 becomes leader at Epoch 1
    token_1 = coord_1.request_leadership()
    assert token_1.epoch == 1
    writer.validate_write(token_1)

    # Failover occurs: Node 2 becomes leader at Epoch 2
    coord_2._current_epoch = coord_1._current_epoch  # sync epoch
    token_2 = coord_2.request_leadership()
    assert token_2.epoch == 2
    writer.validate_write(token_2)
    assert writer.highest_epoch == 2

    # Old Node 1 attempts to write with stale token_1 (Epoch 1)
    with pytest.raises(FencingTokenError) as exc_info:
        writer.validate_write(token_1)
    assert "Stale writer detected" in str(exc_info.value)
    assert writer.stale_writes_rejected == 1


def test_lease_expiration_rejection():
    nodes = ["node_1", "node_2", "node_3"]
    coord = ConsensusCoordinator("node_1", nodes, lease_duration_sec=0.05)
    writer = FencedWALWriter("partition_FX")

    token = coord.request_leadership()
    # Wait for lease to expire
    time.sleep(0.08)
    assert token.is_expired

    with pytest.raises(FencingTokenError) as exc_info:
        writer.validate_write(token)
    assert "Epoch lease expired" in str(exc_info.value)
    assert writer.stale_writes_rejected == 1


def test_network_partition_quorum_loss():
    nodes = ["node_1", "node_2", "node_3"]
    coord = ConsensusCoordinator("node_1", nodes, lease_duration_sec=1.0)

    # Simulate network partition disconnecting node_2 and node_3 from node_1
    coord.simulate_network_partition(["node_2", "node_3"])

    # Node 1 cannot acquire leadership without quorum (only 1 of 3 reachable, quorum = 2)
    with pytest.raises(QuorumLossError):
        coord.request_leadership()

    # Heal partition
    coord.heal_network_partition()
    token = coord.request_leadership()
    assert token.epoch >= 1
    assert token.leader_id == "node_1"


def test_simulated_failover_and_recovery():
    nodes = ["node_A", "node_B", "node_C"]
    coord_A = ConsensusCoordinator("node_A", nodes, lease_duration_sec=0.2)
    coord_B = ConsensusCoordinator("node_B", nodes, lease_duration_sec=0.2)
    writer = FencedWALWriter("partition_CRYPTO")

    # Step 1: Node A leads, writes 10 frames
    token_A = coord_A.request_leadership()
    for _ in range(10):
        writer.validate_write(token_A)
    assert writer.highest_epoch == 1

    # Step 2: Node A gets partitioned
    coord_A.simulate_network_partition(["node_B", "node_C"])
    with pytest.raises(QuorumLossError):
        coord_A.renew_lease()

    # Step 3: Node B detects failure, acquires leadership at Epoch 2
    coord_B._current_epoch = 1
    token_B = coord_B.request_leadership()
    assert token_B.epoch == 2
    for _ in range(10):
        writer.validate_write(token_B)
    assert writer.highest_epoch == 2

    # Step 4: Stale Node A attempts to write -> fenced!
    with pytest.raises(FencingTokenError):
        writer.validate_write(token_A)

    st = writer.stats()
    assert st["valid_writes_accepted"] == 20
    assert st["stale_writes_rejected"] == 1
    assert st["active_leader"] == "node_B"
