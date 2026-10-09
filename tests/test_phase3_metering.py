"""Tests for Phase 3 Workstream H — Licensing, Entitlements, and Compliance Accounting."""

import json
import os
import time
import pytest
from mdrap.metering import DurableUsageMeter, MeteringUnit
from mdrap.security import ClientEntitlement, Role


def test_entitlement_verification_fail_closed():
    """Entitlement verification fails closed for missing, inactive, expired, or unauthorized access."""
    meter = DurableUsageMeter(":memory:")

    # 1. Missing entitlement
    assert meter.verify_entitlement(None, "NASDAQ", "AAPL") is False

    # 2. Inactive / revoked entitlement
    ent_revoked = ClientEntitlement(
        client_id="ClientA",
        token="token_a",
        is_active=False,
    )
    assert meter.verify_entitlement(ent_revoked, "NASDAQ", "AAPL") is False

    # 3. Expired entitlement
    ent_expired = ClientEntitlement(
        client_id="ClientB",
        token="token_b",
        expires_at=time.time() - 100.0,
    )
    assert meter.verify_entitlement(ent_expired, "NASDAQ", "AAPL") is False

    # 4. Source restriction breach
    ent_restricted_source = ClientEntitlement(
        client_id="ClientC",
        token="token_c",
        allowed_sources=["NASDAQ"],
    )
    assert meter.verify_entitlement(ent_restricted_source, "BINANCE", "AAPL") is False
    assert meter.verify_entitlement(ent_restricted_source, "NASDAQ", "AAPL") is True

    # 5. Symbol restriction breach
    ent_restricted_symbol = ClientEntitlement(
        client_id="ClientD",
        token="token_d",
        allowed_symbols=["AAPL", "MSFT"],
    )
    assert meter.verify_entitlement(ent_restricted_symbol, "NASDAQ", "GOOGL") is False
    assert meter.verify_entitlement(ent_restricted_symbol, "NASDAQ", "AAPL") is True


def test_durable_usage_recording_and_idempotency(tmp_path):
    """Usage accounting records units durably and deduplicates identical idempotency keys."""
    db_file = str(tmp_path / "metering.db")
    meter = DurableUsageMeter(db_file)

    # Record first batch
    res1 = meter.record_usage(
        client_id="Firm_Alpha",
        tenant_id="Tenant_1",
        source="NASDAQ",
        symbol="AAPL",
        count=500,
        idempotency_key="batch_001_20261009",
    )
    assert res1 is True

    # Replay same batch (idempotent duplicate)
    res2 = meter.record_usage(
        client_id="Firm_Alpha",
        tenant_id="Tenant_1",
        source="NASDAQ",
        symbol="AAPL",
        count=500,
        idempotency_key="batch_001_20261009",
    )
    assert res2 is False

    # Second distinct batch
    res3 = meter.record_usage(
        client_id="Firm_Alpha",
        tenant_id="Tenant_1",
        source="NASDAQ",
        symbol="AAPL",
        count=250,
        idempotency_key="batch_002_20261009",
    )
    assert res3 is True

    assert meter.get_client_total("Firm_Alpha") == 750

    meter.close()


def test_restart_durability(tmp_path):
    """Usage records survive process termination and database reopen."""
    db_file = str(tmp_path / "durable_meter.db")

    meter1 = DurableUsageMeter(db_file)
    meter1.record_usage(
        client_id="Client_Beta",
        tenant_id="Tenant_Corp",
        source="CME",
        symbol="ES",
        count=1200,
        idempotency_key="cme_es_batch_1",
    )
    meter1.close()

    # Reopen database in fresh instance
    meter2 = DurableUsageMeter(db_file)
    assert meter2.get_client_total("Client_Beta") == 1200

    summary = meter2.get_summary()
    assert len(summary) == 1
    assert summary[0]["tenant_id"] == "Tenant_Corp"
    assert summary[0]["total_count"] == 1200
    meter2.close()


def test_report_exports_json_and_csv(tmp_path):
    """Compliance reports export cleanly to JSON and CSV formats."""
    meter = DurableUsageMeter(":memory:")
    meter.record_usage("C1", "T1", "BINANCE", "BTC-USDT", 100, "tx_1")
    meter.record_usage("C2", "T1", "BINANCE", "ETH-USDT", 200, "tx_2")

    # JSON export
    raw_json = meter.export_report_json()
    parsed = json.loads(raw_json)
    assert parsed["report_version"] == "1.0"
    assert len(parsed["line_items"]) == 2

    # CSV export
    raw_csv = meter.export_report_csv()
    lines = [line.strip() for line in raw_csv.strip().split("\n") if line.strip()]
    assert len(lines) == 3  # Header + 2 rows
    assert "tenant_id,client_id,source,symbol,unit,total_count,batch_count" in lines[0]
