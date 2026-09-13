"""
Tests for Universal Symbology Normalizer across Indian, German, Japanese, and US Markets.
"""
import pytest
from symbology import resolve_symbol, get_native_currency, GLOBAL_SECURITY_DIRECTORY


def test_resolve_indian_equities():
    """Verify Indian equities resolve to NSE MIC with INR currency."""
    info = resolve_symbol("RELIANCE")
    assert info.canonical_id == "RELIANCE.NS"
    assert info.venue_mic == "XNSE"
    assert info.currency == "INR"
    assert info.country == "IN"
    assert info.isin == "INE002A01018"
    assert info.bloomberg == "RELIANCE:IN"

    # With .NS suffix explicitly
    info2 = resolve_symbol("TCS.NS")
    assert info2.canonical_id == "TCS.NS"
    assert info2.venue_mic == "XNSE"
    assert info2.currency == "INR"


def test_resolve_german_equities():
    """Verify German equities resolve to XETRA MIC with EUR currency."""
    info = resolve_symbol("SAP")
    assert info.canonical_id == "SAP.DE"
    assert info.venue_mic == "XETR"
    assert info.currency == "EUR"
    assert info.country == "DE"
    assert info.isin == "DE0007164600"
    assert info.ric == "SAPG.DE"

    info_bmw = resolve_symbol("BMW.DE")
    assert info_bmw.canonical_id == "BMW.DE"
    assert info_bmw.currency == "EUR"


def test_resolve_japanese_equities():
    """Verify Japanese 4-digit codes resolve to TSE MIC with JPY currency."""
    info = resolve_symbol("7203")
    assert info.canonical_id == "7203.T"
    assert info.venue_mic == "XTKS"
    assert info.currency == "JPY"
    assert info.country == "JP"
    assert info.isin == "JP3633400001"

    # Named alias 'TOYOTA' -> 7203
    info_named = resolve_symbol("TOYOTA")
    assert info_named.canonical_id == "7203.T"
    assert info_named.currency == "JPY"

    # Named alias 'SONY' -> 6758
    info_sony = resolve_symbol("SONY")
    assert info_sony.canonical_id == "6758.T"
    assert info_sony.currency == "JPY"


def test_resolve_us_equities():
    """Verify US equities resolve to NASDAQ/NYSE with USD currency."""
    info = resolve_symbol("AAPL")
    assert info.canonical_id == "AAPL"
    assert info.venue_mic == "XNAS"
    assert info.currency == "USD"
    assert info.country == "US"


def test_get_native_currency():
    """Verify helper returns correct ISO currency for various tickers."""
    assert get_native_currency("RELIANCE") == "INR"
    assert get_native_currency("INFY.NS") == "INR"
    assert get_native_currency("SAP") == "EUR"
    assert get_native_currency("BMW.DE") == "EUR"
    assert get_native_currency("7203") == "JPY"
    assert get_native_currency("6758.T") == "JPY"
    assert get_native_currency("NVDA") == "USD"
    assert get_native_currency("SHEL.L") == "GBP"
