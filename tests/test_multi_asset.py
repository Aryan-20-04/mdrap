"""
Tests for multi-asset class support in models.py (Gap 11).
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest
from models import (
    AssetClass,
    FuturesContract,
    OptionDerivative,
    BondSecurity,
    CanonicalEvent,
    EventType,
    QualityStatus
)


def test_asset_class_enum():
    assert AssetClass.EQUITY == "EQUITY"
    assert AssetClass.CRYPTO == "CRYPTO"
    assert AssetClass.FUTURES == "FUTURES"
    assert AssetClass.OPTIONS == "OPTIONS"
    assert AssetClass.BOND == "BOND"
    assert AssetClass.FX == "FX"


def test_futures_contract_model():
    fc = FuturesContract(
        symbol="ESM26",
        underlying="SPX",
        expiry_date="2026-06-19",
        contract_size=50.0,
        tick_size=0.25,
        settlement_type="CASH"
    )
    assert fc.symbol == "ESM26"
    assert fc.contract_size == 50.0
    assert fc.settlement_type == "CASH"


def test_option_derivative_model():
    opt = OptionDerivative(
        symbol="AAPL260619C00200000",
        underlying="AAPL",
        strike=200.0,
        expiry_date="2026-06-19",
        option_type="CALL",
        contract_multiplier=100.0
    )
    assert opt.strike == 200.0
    assert opt.option_type == "CALL"
    assert opt.contract_multiplier == 100.0


def test_bond_security_model():
    bond = BondSecurity(
        cusip="912828XX1",
        issuer="US_TREASURY",
        coupon_rate=4.25,
        maturity_date="2036-05-15",
        face_value=1000.0,
        payment_frequency=2
    )
    assert bond.cusip == "912828XX1"
    assert bond.coupon_rate == 4.25
    assert bond.payment_frequency == 2


def test_canonical_event_multi_asset_extensions():
    # Equity default
    eq_event = CanonicalEvent(
        event_id="e1",
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=1000.0,
        receive_timestamp=1000.1,
        processing_timestamp=1000.2,
        source="NASDAQ",
        sequence_number=1,
        price=150.0,
        quantity=100.0
    )
    assert eq_event.asset_class == AssetClass.EQUITY
    assert eq_event.strike is None

    # Futures event
    fut_event = CanonicalEvent(
        event_id="e2",
        instrument_id="ESM26",
        event_type=EventType.TRADE,
        exchange_timestamp=1000.0,
        receive_timestamp=1000.1,
        processing_timestamp=1000.2,
        source="CME",
        sequence_number=2,
        price=5200.0,
        quantity=5.0,
        asset_class=AssetClass.FUTURES,
        expiry_date="2026-06-19",
        contract_size=50.0,
        underlying_id="SPX",
        open_interest=150000.0
    )
    assert fut_event.asset_class == AssetClass.FUTURES
    assert fut_event.contract_size == 50.0
    assert fut_event.open_interest == 150000.0

    # Options event
    opt_event = CanonicalEvent(
        event_id="e3",
        instrument_id="AAPL260619C00200000",
        event_type=EventType.QUOTE,
        exchange_timestamp=1000.0,
        receive_timestamp=1000.1,
        processing_timestamp=1000.2,
        source="OPRA",
        sequence_number=3,
        bid_price=8.50,
        ask_price=8.70,
        asset_class=AssetClass.OPTIONS,
        underlying_id="AAPL",
        strike=200.0,
        put_call="CALL",
        implied_vol=0.22,
        delta=0.48,
        gamma=0.03
    )
    assert opt_event.asset_class == AssetClass.OPTIONS
    assert opt_event.strike == 200.0
    assert opt_event.delta == 0.48

    # Bond event
    bond_event = CanonicalEvent(
        event_id="e4",
        instrument_id="US10Y",
        event_type=EventType.TRADE,
        exchange_timestamp=1000.0,
        receive_timestamp=1000.1,
        processing_timestamp=1000.2,
        source="TRACE",
        sequence_number=4,
        price=98.50,
        quantity=10000.0,
        asset_class=AssetClass.BOND,
        coupon=4.25,
        maturity_date="2036-05-15",
        yield_to_worst=4.42,
        duration=7.8
    )
    assert bond_event.asset_class == AssetClass.BOND
    assert bond_event.yield_to_worst == 4.42
    assert bond_event.duration == 7.8
