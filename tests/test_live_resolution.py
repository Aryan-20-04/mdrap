"""
Tests for International Ticker Resolution, Zero-Fake-Data Enforcement, and Multi-Currency Display.
Validates:
1. International exchange suffix preservation (.NS, .BO, .L, .TO, .DE).
2. US share class hyphenation (BRK.B -> BRK-B).
3. Tata Motors & Indian market resolution (TMPV -> TMPV.NS).
4. Strict zero-artificial-data policy (fallback_sim=False produces empty lists on 404).
5. Currency symbol mapping and terminal rendering (INR ₹, EUR €, GBP £, USD $).
"""
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from live import resolve_venue_symbols, LiveConnector, INTERNATIONAL_EXCHANGE_SUFFIXES
from terminal_display import get_currency_symbol, render_candlestick_chart, LiveTickerDashboard


class TestLiveTickerResolution(unittest.TestCase):
    def test_international_exchange_suffix_preservation(self):
        """Global exchange suffixes must preserve the dot '.' for Yahoo Finance."""
        cases = [
            ("TMPV.NS", "TMPV.NS"),
            ("TMPV.BO", "TMPV.BO"),
            ("TATAMOTORS.NS", "TATAMOTORS.NS"),
            ("RELIANCE.NS", "RELIANCE.NS"),
            ("TCS.NS", "TCS.NS"),
            ("VOD.L", "VOD.L"),
            ("SHOP.TO", "SHOP.TO"),
            ("BMW.DE", "BMW.DE"),
            ("AIR.PA", "AIR.PA"),
            ("700.HK", "700.HK"),
            ("BHP.AX", "BHP.AX"),
        ]
        for input_sym, expected_yahoo in cases:
            res = resolve_venue_symbols(input_sym)
            self.assertEqual(res["type"], "EQUITY")
            self.assertEqual(res["yahoo"], expected_yahoo, f"Failed for {input_sym}")

    def test_us_share_class_hyphenation(self):
        """US share classes with dot notation must be converted to hyphens for Yahoo Finance."""
        cases = [
            ("BRK.B", "BRK-B"),
            ("BRK.A", "BRK-A"),
            ("BF.B", "BF-B"),
            ("BF.A", "BF-A"),
        ]
        for input_sym, expected_yahoo in cases:
            res = resolve_venue_symbols(input_sym)
            self.assertEqual(res["type"], "EQUITY")
            self.assertEqual(res["yahoo"], expected_yahoo, f"Failed for {input_sym}")

    def test_known_equities_resolution(self):
        """Pre-curated aliases like TMPV, TATAMOTORS, VTI resolve immediately."""
        self.assertEqual(resolve_venue_symbols("TMPV")["yahoo"], "TMPV.NS")
        self.assertEqual(resolve_venue_symbols("TATAMOTORS")["yahoo"], "TATAMOTORS.NS")
        self.assertEqual(resolve_venue_symbols("VTI")["yahoo"], "VTI")
        self.assertEqual(resolve_venue_symbols("AAPL")["yahoo"], "AAPL")
        self.assertEqual(resolve_venue_symbols("RELIANCE")["yahoo"], "RELIANCE.NS")

    def test_zero_fake_data_policy_on_404(self):
        """When live market data returns 404, LiveConnector must NOT fabricate artificial data unless explicitly requested."""
        connector = LiveConnector()
        # Patch _get_json to simulate 404
        with patch.object(connector, "_get_json", return_value=None):
            # Default: fallback_sim=False -> strictly zero fake data
            evs_real = connector.fetch_equity_events("UNKNOWN_NONEXISTENT_999", fallback_sim=False)
            self.assertEqual(len(evs_real), 0)

            # Explicit simulation mode: fallback_sim=True -> generates synthetic ticks
            evs_sim = connector.fetch_equity_events("UNKNOWN_NONEXISTENT_999", fallback_sim=True)
            self.assertGreater(len(evs_sim), 0)
            self.assertTrue(evs_sim[0].payload.get("is_simulated"))

    def test_stream_ticks_strict_mode(self):
        """stream_ticks with fallback_sim=False must not yield synthetic ticks on missing data."""
        connector = LiveConnector()
        with patch.object(connector, "_get_json", return_value=None):
            ticks = list(connector.stream_ticks(["UNKNOWN_SYM_999"], limit=5, fallback_sim=False, poll_interval_s=0.001, max_empty_polls=2))
            self.assertEqual(len(ticks), 0)

    def test_probe_or_resolve_equity_fallback(self):
        """Online prober checks international suffixes when bare ticker is not found on US exchanges."""
        connector = LiveConnector()

        def mock_get_json(url: str):
            if "TMPV.NS" in url:
                return {
                    "chart": {
                        "result": [{
                            "meta": {
                                "symbol": "TMPV.NS",
                                "currency": "INR",
                                "exchangeName": "NSI",
                                "regularMarketPrice": 301.10,
                            }
                        }]
                    }
                }
            return None

        with patch.object(connector, "_get_json", side_effect=mock_get_json):
            # Clear cache for isolated test
            connector._online_symbol_cache.clear()
            resolved = connector.probe_or_resolve_equity("SOMEUNSUFFIXED")
            self.assertIsNone(resolved)

            # Probing TMPV should resolve to TMPV.NS
            res = connector.probe_or_resolve_equity("TMPV")
            self.assertIsNotNone(res)
            ticker, meta = res
            self.assertEqual(ticker, "TMPV.NS")
            self.assertEqual(meta["currency"], "INR")
            self.assertEqual(meta["regularMarketPrice"], 301.10)


class TestMultiCurrencyFormatting(unittest.TestCase):
    def test_get_currency_symbol(self):
        """Verify currency code and ticker suffix mapping."""
        self.assertEqual(get_currency_symbol("INR"), "₹")
        self.assertEqual(get_currency_symbol("USD"), "$")
        self.assertEqual(get_currency_symbol("EUR"), "€")
        self.assertEqual(get_currency_symbol("GBP"), "£")
        self.assertEqual(get_currency_symbol("JPY"), "¥")
        self.assertEqual(get_currency_symbol("CAD"), "C$")

        # By instrument suffix
        self.assertEqual(get_currency_symbol(None, "TMPV.NS"), "₹")
        self.assertEqual(get_currency_symbol(None, "TMPV.BO"), "₹")
        self.assertEqual(get_currency_symbol(None, "RELIANCE.NS"), "₹")
        self.assertEqual(get_currency_symbol(None, "BMW.DE"), "€")
        self.assertEqual(get_currency_symbol(None, "VOD.L"), "£")
        self.assertEqual(get_currency_symbol(None, "SHOP.TO"), "C$")
        self.assertEqual(get_currency_symbol(None, "AAPL"), "$")
        self.assertEqual(get_currency_symbol(None, "BTC/USD"), "$")

    def test_candlestick_chart_currency_rendering(self):
        """Candlestick chart summary and price axis should display the correct currency symbol."""
        candles = [
            {"open": 300.0, "high": 305.0, "low": 298.0, "close": 302.0, "volume": 1000, "bucket_start": 1000.0},
            {"open": 302.0, "high": 306.0, "low": 301.0, "close": 304.5, "volume": 1500, "bucket_start": 1005.0},
        ]
        chart_inr = render_candlestick_chart(candles, currency_symbol="₹")
        self.assertIn("₹", chart_inr)
        self.assertNotIn("$", chart_inr)

        chart_usd = render_candlestick_chart(candles, currency_symbol="$")
        self.assertIn("$", chart_usd)
        self.assertNotIn("₹", chart_usd)


class TestMultiVenueEquityIntegration(unittest.TestCase):
    def test_resolve_equity_venues_dual_listing(self):
        """TMPV should resolve to both NSE (TMPV.NS) and BSE (TMPV.BO) venues."""
        connector = LiveConnector()
        with patch.object(connector, "probe_or_resolve_equity") as mock_probe:
            def _probe(sym: str):
                if sym == "TMPV.NS":
                    return ("TMPV.NS", {"exchangeName": "NSI", "currency": "INR", "regularMarketPrice": 301.10})
                elif sym == "TMPV.BO":
                    return ("TMPV.BO", {"exchangeName": "BSE", "currency": "INR", "regularMarketPrice": 301.80})
                return None
            mock_probe.side_effect = _probe

            venues = connector.resolve_equity_venues("TMPV")
            self.assertEqual(len(venues), 2)
            vnames = [v[1] for v in venues]
            self.assertIn("NSE", vnames)
            self.assertIn("BSE", vnames)

    def test_trade_event_does_not_clobber_venue_quotes(self):
        """A subsequent TRADE event with zero bid/ask must not clobber existing venue quotes with dashes."""
        from models import RawEvent
        dashboard = LiveTickerDashboard()

        # 1. Quote event arrives with bid=300.95 and ask=301.25
        q_raw = RawEvent(
            source="NSE",
            payload={
                "instrument": "TMPV",
                "event_type": "QUOTE",
                "exchange": "NSE",
                "bid": 300.95,
                "ask": 301.25,
                "price": 301.10,
            },
            receive_timestamp=1000.0,
            raw_id="raw-1",
        )
        dashboard.update_with_event(q_raw, None, engine_ns=50_000)

        # 2. Trade event arrives immediately with price=301.10 (no bid or ask)
        t_raw = RawEvent(
            source="NSE",
            payload={
                "instrument": "TMPV",
                "event_type": "TRADE",
                "exchange": "NSE",
                "price": 301.10,
                "quantity": 100.0,
            },
            receive_timestamp=1000.01,
            raw_id="raw-2",
        )
        dashboard.update_with_event(t_raw, None, engine_ns=45_000)

        # Verify quote data is preserved!
        vq = dashboard.venue_quotes["TMPV"]["NSE"]
        self.assertEqual(vq["bid"], 300.95)
        self.assertEqual(vq["ask"], 301.25)
        self.assertAlmostEqual(vq["spread"], 0.30, places=4)


if __name__ == "__main__":
    unittest.main()
