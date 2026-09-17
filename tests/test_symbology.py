"""
Tests for Universal Symbology Normalizer across Indian, German, Japanese, and US Markets.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from symbology import get_native_currency, resolve_symbol


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


def test_resolve_as_of_date_prevents_lookahead_bias():
    """Verify historical corporate action / ticker rename prevents lookahead bias (Invariant Q5)."""
    # Before FB -> META rename (effective 2022-06-09): querying META on 2021 should resolve to FB
    info_pre = resolve_symbol("META", as_of_date="2021-06-01")
    assert info_pre.ticker == "FB"
    assert info_pre.canonical_id == "FB"

    # After FB -> META rename: querying FB on 2023 should resolve to META
    info_post = resolve_symbol("FB", as_of_date="2023-01-01")
    assert info_post.ticker == "META"
    assert info_post.canonical_id == "META"

    # Querying without as_of_date resolves as provided
    assert resolve_symbol("META").ticker == "META"
    assert resolve_symbol("FB").ticker == "FB"


def test_resolve_apac_suffixes_and_futures():
    """Verify regional APAC suffixes (.SI, .KS, .TW, .PA) and benchmark futures resolution."""
    # Singapore (.SI)
    si = resolve_symbol("DBS.SI")
    assert si.venue_mic == "XSES"
    assert si.currency == "SGD"

    # Korea (.KS)
    ks = resolve_symbol("005930.KS")
    assert ks.venue_mic == "XKRX"
    assert ks.currency == "KRW"

    # Taiwan (.TW)
    tw = resolve_symbol("2330.TW")
    assert tw.venue_mic == "XTWS"
    assert tw.currency == "TWD"

    # Paris (.PA)
    pa = resolve_symbol("MC.PA")
    assert pa.venue_mic == "XPAR"
    assert pa.currency == "EUR"

    # Benchmark futures
    es = resolve_symbol("ES")
    assert es.canonical_id == "ES.CME"
    assert es.venue_mic == "XCME"
    assert es.currency == "USD"

    cl = resolve_symbol("CL")
    assert cl.canonical_id == "CL.NYM"
    assert cl.venue_mic == "XNYM"
