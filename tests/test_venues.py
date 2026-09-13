"""
Tests for Exchange Venue Registry, Market Schedules, and Microstructure.
"""
import pytest
import datetime
from venues import (
    GLOBAL_VENUES,
    MarketPhase,
    TickSizeModel,
    get_venue,
    get_session_phase,
    get_tick_size,
    format_currency,
)


def test_venue_registry_mics():
    """Verify all required global market venues are registered with correct currencies."""
    assert "XNSE" in GLOBAL_VENUES
    assert GLOBAL_VENUES["XNSE"].currency == "INR"
    assert GLOBAL_VENUES["XNSE"].currency_symbol == "₹"

    assert "XETR" in GLOBAL_VENUES
    assert GLOBAL_VENUES["XETR"].currency == "EUR"
    assert GLOBAL_VENUES["XETR"].currency_symbol == "€"

    assert "XTKS" in GLOBAL_VENUES
    assert GLOBAL_VENUES["XTKS"].currency == "JPY"
    assert GLOBAL_VENUES["XTKS"].currency_symbol == "¥"

    assert "XNAS" in GLOBAL_VENUES
    assert GLOBAL_VENUES["XNAS"].currency == "USD"
    assert GLOBAL_VENUES["XNAS"].currency_symbol == "$"


def test_get_venue_aliases():
    """Verify case-insensitive alias lookups resolve to canonical MICs."""
    assert get_venue("nse").mic == "XNSE"
    assert get_venue("INDIA").mic == "XNSE"
    assert get_venue("xetra").mic == "XETR"
    assert get_venue("germany").mic == "XETR"
    assert get_venue("tse").mic == "XTKS"
    assert get_venue("tokyo").mic == "XTKS"
    assert get_venue("japan").mic == "XTKS"
    assert get_venue("nasdaq").mic == "XNAS"
    assert get_venue("unknown_venue").mic == "XNAS"


def test_session_phase_weekend():
    """Verify weekend detection flags markets as closed."""
    # Saturday 2026-09-12 12:00 UTC
    sat_dt = datetime.datetime(2026, 9, 12, 12, 0, tzinfo=datetime.timezone.utc)
    sat_ts = sat_dt.timestamp()

    for venue in (get_venue("XNSE"), get_venue("XETR"), get_venue("XTKS"), get_venue("XNAS")):
        is_open, phase, desc = get_session_phase(venue, sat_ts)
        assert not is_open
        assert phase == MarketPhase.CLOSED
        assert "Weekend" in desc


def test_session_phase_continuous():
    """Verify market open detection during continuous trading hours."""
    # Wednesday 2026-09-09 05:00 UTC
    # 05:00 UTC = 10:30 IST (NSE open), 14:00 JST (TSE open), 06:00 CET (Xetra closed)
    wed_dt = datetime.datetime(2026, 9, 9, 5, 0, tzinfo=datetime.timezone.utc)
    wed_ts = wed_dt.timestamp()

    is_open_nse, phase_nse, _ = get_session_phase(get_venue("XNSE"), wed_ts)
    assert is_open_nse
    assert phase_nse == MarketPhase.CONTINUOUS

    is_open_tse, phase_tse, _ = get_session_phase(get_venue("XTKS"), wed_ts)
    assert is_open_tse
    assert phase_tse == MarketPhase.CONTINUOUS

    is_open_xetra, phase_xetra, _ = get_session_phase(get_venue("XETR"), wed_ts)
    assert not is_open_xetra


def test_tick_sizes_nse_dynamic():
    """Verify Indian NSE dynamic tick size: ₹0.01 for <=₹250, ₹0.05 for >₹250."""
    assert get_tick_size(150.0, TickSizeModel.NSE_DYNAMIC) == 0.01
    assert get_tick_size(250.0, TickSizeModel.NSE_DYNAMIC) == 0.01
    assert get_tick_size(250.05, TickSizeModel.NSE_DYNAMIC) == 0.05
    assert get_tick_size(2450.0, TickSizeModel.NSE_DYNAMIC) == 0.05


def test_tick_sizes_mifid2():
    """Verify European MiFID II RTS 28 liquidity tick sizes."""
    assert get_tick_size(0.40, TickSizeModel.MIFID2_RTS28) == 0.0001
    assert get_tick_size(15.0, TickSizeModel.MIFID2_RTS28) == 0.005
    assert get_tick_size(150.0, TickSizeModel.MIFID2_RTS28) == 0.05
    assert get_tick_size(600.0, TickSizeModel.MIFID2_RTS28) == 0.20


def test_tick_sizes_tse_tiered():
    """Verify Japanese TSE TOPIX100 tiered tick sizes."""
    assert get_tick_size(850.0, TickSizeModel.TSE_TIERED) == 0.1
    assert get_tick_size(2500.0, TickSizeModel.TSE_TIERED) == 0.5
    assert get_tick_size(8000.0, TickSizeModel.TSE_TIERED) == 1.0
    assert get_tick_size(15000.0, TickSizeModel.TSE_TIERED) == 5.0


def test_currency_formatting():
    """Verify localized currency glyphs and grouping conventions."""
    # INR Lakh/Crore grouping
    assert format_currency(125000.0, "INR") == "₹1,25,000.00"
    assert format_currency(10000000.0, "INR") == "₹1,00,00,000.00"

    # JPY integer formatting
    assert format_currency(3500.0, "JPY") == "¥3,500"

    # EUR standard formatting
    assert format_currency(1450.50, "EUR") == "€1,450.50"

    # USD standard formatting
    assert format_currency(195.25, "USD") == "$195.25"
