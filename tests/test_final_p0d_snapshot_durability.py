"""Final Adversarial Test Suite for P0-D: Snapshot Durability, Corruption Fallback, and Fail-Closed WAL Invariants."""

import pytest
from tests.final.test_multiple_snapshot_corruption import (
    test_cascading_snapshot_corruption_and_fail_closed,
    test_concurrent_ingestion_during_snapshot_creation,
    test_snapshot_atomic_rollback_on_simulated_io_error,
    test_snapshot_checksum_tampering_detection,
)

__all__ = [
    "test_cascading_snapshot_corruption_and_fail_closed",
    "test_concurrent_ingestion_during_snapshot_creation",
    "test_snapshot_atomic_rollback_on_simulated_io_error",
    "test_snapshot_checksum_tampering_detection",
]
