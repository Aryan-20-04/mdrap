"""
MDRAP Multi-Currency Denomination-Aware Valuation & FX Basis Engine (§26, v5.3).

Addresses the cross-venue arbitrage fallacy (Forensic Finding §3.4):
Raw cross-exchange quote differences (e.g. Coinbase BTC-USD vs Binance BTC-USDT)
are not risk-free arbitrage opportunities unless normalized for:
1. Currency Basis (e.g., USDT/USD discount or premium, USDC/USD peg).
2. Bilateral Exchange Trading Fees (Maker/Taker fees on both legs).
3. Transfer / Withdrawal / Settlement Latency & Slippage Buffers.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ArbitrageSpreadAnalysis:
    is_profitable: bool
    gross_spread_usd: float
    gross_spread_bps: float
    total_fees_usd: float
    net_spread_usd: float
    net_spread_bps: float
    buy_venue: str
    buy_price_native: float
    buy_price_usd: float
    sell_venue: str
    sell_price_native: float
    sell_price_usd: float
    fx_rate_applied: float
    rejection_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_profitable": self.is_profitable,
            "gross_spread_usd": round(self.gross_spread_usd, 4),
            "gross_spread_bps": round(self.gross_spread_bps, 2),
            "total_fees_usd": round(self.total_fees_usd, 4),
            "net_spread_usd": round(self.net_spread_usd, 4),
            "net_spread_bps": round(self.net_spread_bps, 2),
            "buy_venue": self.buy_venue,
            "buy_price_native": round(self.buy_price_native, 4),
            "buy_price_usd": round(self.buy_price_usd, 4),
            "sell_venue": self.sell_venue,
            "sell_price_native": round(self.sell_price_native, 4),
            "sell_price_usd": round(self.sell_price_usd, 4),
            "fx_rate_applied": round(self.fx_rate_applied, 6),
            "rejection_reason": self.rejection_reason,
        }


class DenominationAwareValuator:
    """
    Normalizes multi-currency quotes to a canonical base currency (USD)
    and evaluates honest, executable net arbitrage profitability.
    """

    def __init__(
        self,
        base_currency: str = "USD",
        default_rates: dict[str, float] | None = None,
        min_profit_threshold_bps: float = 2.0,  # Minimum net spread required to trigger execution
    ):
        self.base_currency = base_currency.upper()
        self.min_profit_threshold_bps = min_profit_threshold_bps
        # FX rates against base_currency (e.g. USDT -> 0.9990 USD)
        self._rates: dict[str, float] = {
            "USD": 1.0000,
            "USDT": 0.9990,  # Typical tether basis discount
            "USDC": 1.0000,
            "EUR": 1.0850,
            "GBP": 1.2950,
        }
        if default_rates:
            self._rates.update(default_rates)

    def set_fx_rate(self, currency: str, rate_to_base: float) -> None:
        """Update live FX basis rate against base currency."""
        self._rates[currency.upper()] = max(1e-6, float(rate_to_base))

    def get_fx_rate(self, currency: str) -> float:
        """Retrieve current FX basis rate to base currency."""
        return self._rates.get(currency.upper(), 1.0)

    def to_base_price(self, price: float, quote_currency: str) -> float:
        """Convert native price to base currency (USD)."""
        rate = self.get_fx_rate(quote_currency)
        return price * rate

    def parse_currency(self, symbol: str) -> tuple[str, str]:
        """Extract base and quote currencies from standard symbol formats (BTC-USD, BTC/USDT, BTCUSDT)."""
        clean = symbol.upper().replace("/", "-").replace("_", "-")
        if "-" in clean:
            parts = clean.split("-")
            return parts[0], parts[1]
        for known_quote in ("USDT", "USDC", "USD", "EUR", "GBP"):
            if clean.endswith(known_quote):
                base = clean[: -len(known_quote)]
                return base, known_quote
        return clean, self.base_currency

    def analyze_cross_venue_spread(
        self,
        buy_venue: str,
        buy_symbol: str,
        buy_price: float,
        buy_taker_fee_bps: float,
        sell_venue: str,
        sell_symbol: str,
        sell_price: float,
        sell_taker_fee_bps: float,
        slippage_buffer_bps: float = 1.0,
    ) -> ArbitrageSpreadAnalysis:
        """
        Evaluate true net executable arbitrage spread across two venues.

        Formula:
        Buy USD = Buy Price Native * FX_Rate(Buy Quote)
        Sell USD = Sell Price Native * FX_Rate(Sell Quote)
        Gross Spread = Sell USD - Buy USD
        Gross Bps = (Gross Spread / Buy USD) * 10,000
        Total Fee Bps = Buy Fee Bps + Sell Fee Bps + Slippage Buffer Bps
        Total Fees USD = Buy USD * (Buy Fee / 10,000) + Sell USD * (Sell Fee / 10,000)
        Net Spread USD = Gross Spread USD - Total Fees USD
        Net Bps = Gross Bps - Total Fee Bps
        """
        _, buy_quote = self.parse_currency(buy_symbol)
        _, sell_quote = self.parse_currency(sell_symbol)

        buy_rate = self.get_fx_rate(buy_quote)
        sell_rate = self.get_fx_rate(sell_quote)

        buy_usd = buy_price * buy_rate
        sell_usd = sell_price * sell_rate

        gross_spread_usd = sell_usd - buy_usd
        gross_spread_bps = (gross_spread_usd / buy_usd * 10_000.0) if buy_usd > 0 else 0.0

        buy_fee_usd = buy_usd * (buy_taker_fee_bps / 10_000.0)
        sell_fee_usd = sell_usd * (sell_taker_fee_bps / 10_000.0)
        slippage_usd = buy_usd * (slippage_buffer_bps / 10_000.0)
        total_fees_usd = buy_fee_usd + sell_fee_usd + slippage_usd

        net_spread_usd = gross_spread_usd - total_fees_usd
        net_spread_bps = (net_spread_usd / buy_usd * 10_000.0) if buy_usd > 0 else 0.0

        is_profitable = net_spread_bps >= self.min_profit_threshold_bps

        reason = "PROFITABLE"
        if gross_spread_usd <= 0:
            reason = "NEGATIVE_GROSS_SPREAD"
        elif buy_rate != sell_rate and (sell_price - buy_price > 0) and gross_spread_usd <= 0:
            reason = "FX_BASIS_EROSION"
        elif not is_profitable:
            reason = "FEE_DRAG_UNPROFITABLE"

        return ArbitrageSpreadAnalysis(
            is_profitable=is_profitable,
            gross_spread_usd=gross_spread_usd,
            gross_spread_bps=gross_spread_bps,
            total_fees_usd=total_fees_usd,
            net_spread_usd=net_spread_usd,
            net_spread_bps=net_spread_bps,
            buy_venue=buy_venue,
            buy_price_native=buy_price,
            buy_price_usd=buy_usd,
            sell_venue=sell_venue,
            sell_price_native=sell_price,
            sell_price_usd=sell_usd,
            fx_rate_applied=sell_rate / buy_rate if buy_rate > 0 else 1.0,
            rejection_reason=reason,
        )
