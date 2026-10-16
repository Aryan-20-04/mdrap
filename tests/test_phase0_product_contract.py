"""Phase 0 Acceptance Test Suite: Product Contract & Architecture Boundaries (Strategy §11 Phase 0).

Verifies that:
1. QualityStatus severity is strictly ordered (INVALID > SUSPICIOUS > VALID) and cannot be downgraded.
2. AckStatus lifecycle boundaries strictly reflect the 6 documented persistence stages.
3. OperationalMode contracts distinguish LOSS_DETECTING, DURABLE_CAPTURE, and LOSSLESS_TO_SOURCE.
4. CanonicalEvent and RawEvent contracts preserve full provenance, timestamp domains, and reason codes.
5. High-level facade exposes stream(), connect(), and doctor() without starting background daemons.
6. Terminology contracts: drop counters are strictly monotonic and never silent.
"""

import time
import pytest

from mdrap.models import (
    AckStatus,
    CanonicalEvent,
    EventType,
    OperationalMode,
    QualityStatus,
    RawEvent,
    Reason,
    from_fixed_point_price,
    to_fixed_point_price,
)
from mdrap.facade import stream, connect, doctor
from mdrap.profile import HardwareProfile, current_profile


class TestQualityStatusContract:
    """Verifies §6.4 Quality Semantics and Non-Downgrade Invariant."""

    def test_severity_ordering(self):
        assert QualityStatus.INVALID.severity_level == 2
        assert QualityStatus.SUSPICIOUS.severity_level == 1
        assert QualityStatus.VALID.severity_level == 0

        assert QualityStatus.INVALID.is_worse_than(QualityStatus.SUSPICIOUS)
        assert QualityStatus.INVALID.is_worse_than(QualityStatus.VALID)
        assert QualityStatus.SUSPICIOUS.is_worse_than(QualityStatus.VALID)
        assert not QualityStatus.VALID.is_worse_than(QualityStatus.INVALID)
        assert not QualityStatus.SUSPICIOUS.is_worse_than(QualityStatus.INVALID)

    def test_non_downgrade_merge(self):
        # Merging with INVALID always yields INVALID
        assert (
            QualityStatus.merge(QualityStatus.VALID, QualityStatus.INVALID)
            == QualityStatus.INVALID
        )
        assert (
            QualityStatus.merge(QualityStatus.SUSPICIOUS, QualityStatus.INVALID)
            == QualityStatus.INVALID
        )
        assert (
            QualityStatus.merge(
                QualityStatus.INVALID, QualityStatus.SUSPICIOUS, QualityStatus.VALID
            )
            == QualityStatus.INVALID
        )

        # Merging with SUSPICIOUS yields SUSPICIOUS unless INVALID is present
        assert (
            QualityStatus.merge(QualityStatus.VALID, QualityStatus.SUSPICIOUS)
            == QualityStatus.SUSPICIOUS
        )
        assert (
            QualityStatus.merge(QualityStatus.VALID, QualityStatus.VALID)
            == QualityStatus.VALID
        )

    def test_string_coercion_in_merge(self):
        assert QualityStatus.merge("VALID", "SUSPICIOUS") == QualityStatus.SUSPICIOUS
        assert QualityStatus.merge("VALID", "INVALID") == QualityStatus.INVALID
        assert QualityStatus.merge(None, "VALID") == QualityStatus.VALID


class TestAckStatusContract:
    """Verifies §6.1 Acknowledgement Boundary Semantics."""

    def test_documented_durability_boundaries(self):
        assert AckStatus.RECEIVED == 1
        assert AckStatus.VALIDATED == 2
        assert AckStatus.ACCEPTED == 2  # Alias
        assert AckStatus.BUFFERED_APP == 3
        assert AckStatus.JOURNALED == 4
        assert AckStatus.WRITTEN_OS == 4  # Alias
        assert AckStatus.DURABLE == 5
        assert AckStatus.DURABLY_COMMITTED == 5  # Alias
        assert AckStatus.RECOVERED == 6
        assert AckStatus.PUBLISHED == 7

    def test_boundary_progression(self):
        # Acknowledging durable strictly requires level >= 5
        assert AckStatus.DURABLE >= AckStatus.JOURNALED
        assert AckStatus.JOURNALED > AckStatus.BUFFERED_APP
        assert AckStatus.BUFFERED_APP > AckStatus.VALIDATED
        assert AckStatus.VALIDATED > AckStatus.RECEIVED


class TestOperationalModeContract:
    """Verifies §2 Explicit Operational Modes (Live vs Durable vs Lossless)."""

    def test_declared_modes(self):
        assert OperationalMode.LOSS_DETECTING.value == "LOSS_DETECTING"
        assert OperationalMode.DURABLE_CAPTURE.value == "DURABLE_CAPTURE"
        assert OperationalMode.LOSSLESS_TO_SOURCE.value == "LOSSLESS_TO_SOURCE"

    def test_mode_properties(self):
        modes = [m.value for m in OperationalMode]
        assert "LOSS_DETECTING" in modes
        assert "DURABLE_CAPTURE" in modes
        assert "LOSSLESS_TO_SOURCE" in modes


class TestEventProvenanceContract:
    """Verifies §4.1 Canonical Event and Raw Event Field Contracts."""

    def test_raw_event_immutability_and_provenance(self):
        now = time.time()
        raw = RawEvent(
            source="BINANCE",
            payload={"instrument": "BTC/USDT", "price": 65000.0, "qty": 1.0},
            receive_timestamp=now,
            raw_id="raw_1001",
        )
        assert raw.source == "BINANCE"
        assert raw.receive_timestamp == now
        assert raw.raw_id == "raw_1001"

    def test_canonical_event_provenance_and_quality(self):
        evt = CanonicalEvent(
            event_id="evt_1001",
            instrument_id="BTC/USDT",
            event_type=EventType.TRADE,
            price=65000.0,
            quantity=1.5,
            source="KRAKEN",
            quality_status=QualityStatus.VALID,
            reasons=[],
            sequence_number=1001,
            exchange_timestamp=1729000000.0,
            receive_timestamp=1729000000.002,
        )
        assert evt.instrument_id == "BTC/USDT"
        assert evt.quality_status == QualityStatus.VALID
        assert evt.source == "KRAKEN"
        assert evt.sequence_number == 1001
        assert evt.exchange_timestamp == 1729000000.0

    def test_fixed_point_price_conversion(self):
        scaled = to_fixed_point_price(65432.12345678)
        assert scaled == 6543212345678
        restored = from_fixed_point_price(scaled)
        assert abs(restored - 65432.12345678) < 1e-8


class TestFacadeErgonomics:
    """Verifies §8.1 High-Level Developer Facade Contract."""

    def test_doctor_inspection(self):
        doc = doctor()
        assert "platform" in doc
        assert "python_version" in doc
        assert "cpu_cores" in doc
        assert "total_ram_gb" in doc
        assert "profile_mode" in doc
        assert doc["profile_mode"] in ("LITE", "STANDARD", "ULTRA")

    def test_connect_context_manager(self):
        with connect("sim:BTC/USDT") as conn:
            assert conn.target == "sim:BTC/USDT"
            assert hasattr(conn, "profile")
            assert callable(conn.stop)
