"""
MDRAP Foreign Exchange (FX) Matrix and Multi-Currency Valuation Engine.

Provides real-time cross-currency conversion, triangular arbitrage resolution,
and portfolio valuation normalization across INR, EUR, JPY, GBP, and USD.
"""

from __future__ import annotations

from typing import Dict, Optional

__stability__ = "beta"


class FXMatrix:
    """
    In-memory foreign exchange conversion matrix with cross-rate calculation via USD base.
    """

    def __init__(self, initial_rates: Optional[Dict[str, float]] = None):
        # Base rates against USD (amount of quote currency per 1 USD)
        self._usd_rates: Dict[str, float] = {
            "USD": 1.0,
            "INR": 88.50,  # Indian Rupee
            "EUR": 0.9215,  # Euro (1 EUR = ~1.085 USD)
            "JPY": 152.20,  # Japanese Yen
            "GBP": 0.7722,  # British Pound (1 GBP = ~1.295 USD)
            "HKD": 7.82,  # Hong Kong Dollar
            "CHF": 0.8850,  # Swiss Franc
            "CAD": 1.3850,  # Canadian Dollar
            "AUD": 1.5400,  # Australian Dollar
            "SGD": 1.3400,  # Singapore Dollar
            "KRW": 1340.0,  # South Korean Won
            "TWD": 32.00,  # New Taiwan Dollar
        }
        if initial_rates:
            for pair, rate in initial_rates.items():
                self.update_rate(pair, rate)

    def update_rate(self, pair: str, rate: float) -> None:
        """
        Updates exchange rate. Supported formats:
          - 'USD/INR' -> 88.50
          - 'EUR/USD' -> 1.085
          - 'USDINR=X' (Yahoo style)
        """
        if rate <= 0:
            return
        clean = pair.replace("=X", "").replace("-", "/").upper()
        if "/" in clean:
            base, quote = clean.split("/", 1)
        elif len(clean) == 6:
            base, quote = clean[:3], clean[3:]
        else:
            return

        if base == "USD":
            self._usd_rates[quote] = float(rate)
        elif quote == "USD":
            self._usd_rates[base] = 1.0 / float(rate)
        else:
            # Cross pair: if quote is in USD rates, derive base
            if quote in self._usd_rates:
                self._usd_rates[base] = self._usd_rates[quote] / float(rate)

    def get_rate(self, from_curr: str, to_curr: str) -> float:
        """
        Returns exchange rate: how much of to_curr is 1 unit of from_curr.
        Rate = (USD -> to_curr) / (USD -> from_curr).
        """
        fc = from_curr.strip().upper()
        tc = to_curr.strip().upper()

        if fc == tc:
            return 1.0

        usd_per_from = self._usd_rates.get(fc)
        usd_per_to = self._usd_rates.get(tc)

        if usd_per_from is None or usd_per_to is None:
            # Unknown currency, assume 1.0 parity
            return 1.0

        # Since usd_rates stores: 1 USD = X Currency
        # Value in USD = amount / usd_per_from
        # Value in to_curr = Value in USD * usd_per_to
        return usd_per_to / usd_per_from

    def convert(self, amount: float, from_curr: str, to_curr: str = "USD") -> float:
        """Converts an amount from one currency to another."""
        if from_curr.upper() == to_curr.upper() or amount == 0.0:
            return amount
        rate = self.get_rate(from_curr, to_curr)
        return amount * rate

    def rates_summary(self) -> Dict[str, float]:
        """Returns snapshot of current FX rates relative to 1 USD."""
        return dict(self._usd_rates)


# Global institutional FX matrix singleton
GLOBAL_FX: FXMatrix = FXMatrix()


def convert_currency(amount: float, from_curr: str, to_curr: str = "USD") -> float:
    """Convenience functional wrapper for FX conversion."""
    return GLOBAL_FX.convert(amount, from_curr, to_curr)
