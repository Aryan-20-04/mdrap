"""
Tests for Foreign Exchange (FX) Matrix and Currency Conversion Engine.
"""
import pytest
from fx import FXMatrix, convert_currency, GLOBAL_FX


def test_fx_identity():
    """Converting from currency X to X returns exact amount."""
    matrix = FXMatrix()
    assert matrix.convert(100.0, "USD", "USD") == 100.0
    assert matrix.convert(5000.0, "INR", "INR") == 5000.0
    assert matrix.convert(250.0, "EUR", "EUR") == 250.0
    assert matrix.convert(0.0, "USD", "JPY") == 0.0


def test_fx_direct_conversion():
    """Verify standard direct conversions against USD."""
    matrix = FXMatrix({"USD/INR": 88.50, "EUR/USD": 1.085})
    # 100 USD in INR -> 8,850 INR
    assert round(matrix.convert(100.0, "USD", "INR"), 2) == 8850.00
    # 8,850 INR in USD -> 100 USD
    assert round(matrix.convert(8850.0, "INR", "USD"), 2) == 100.00


def test_fx_triangular_arbitrage_cross_rate():
    """Verify triangular cross-rates: EUR -> INR via USD."""
    matrix = FXMatrix({"USD/INR": 88.50, "EUR/USD": 1.10})
    # 1 EUR = 1.10 USD
    # 1.10 USD * 88.50 = 97.35 INR
    eur_to_inr = matrix.convert(100.0, "EUR", "INR")
    assert round(eur_to_inr, 2) == 9735.00


def test_fx_update_rate():
    """Verify dynamic FX update modifies live rates."""
    matrix = FXMatrix()
    matrix.update_rate("USD/JPY", 160.0)
    assert matrix.convert(1.0, "USD", "JPY") == 160.0
    assert matrix.convert(160.0, "JPY", "USD") == 1.0


def test_convert_currency_global():
    """Verify module-level convert_currency function operates cleanly."""
    usd_val = convert_currency(100.0, "USD", "USD")
    assert usd_val == 100.0
    inr_val = convert_currency(100.0, "USD", "INR")
    assert inr_val > 0.0
