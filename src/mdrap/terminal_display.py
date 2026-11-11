"""MDRAP Streamlined Headless Telemetry & Terminal Charting Module.

Provides non-interactive ANSI/Unicode candlestick charting, sparklines,
and headless streaming ticker metrics for operational inspection.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, Dict, List, Optional

from .term import Console, Table, Panel, poll_keypress

__stability__ = "stable"

CURRENCY_SYMBOLS: Dict[str, str] = {
    "USD": "$",
    "INR": "₹",
    "EUR": "€",
    "GBP": "£",
    "JPY": "¥",
    "CAD": "C$",
    "AUD": "A$",
    "HKD": "HK$",
    "CHF": "CHF ",
    "SGD": "S$",
    "BTC": "₿",
    "ETH": "Ξ",
    "SOL": "◎",
}


def get_currency_symbol(
    currency_code: Optional[str] = None, instrument: str = ""
) -> str:
    """Resolve active fiat/asset currency symbol for terminal tables and charts."""
    if currency_code and currency_code.upper() in CURRENCY_SYMBOLS:
        return CURRENCY_SYMBOLS[currency_code.upper()]
    inst = (instrument or "").upper()
    if (
        inst.endswith(".NS")
        or inst.endswith(".BO")
        or inst
        in ("TMPV", "TATAMOTORS", "RELIANCE", "TCS", "INFY", "HDFCBANK", "SBIN", "ITC")
    ):
        return "₹"
    if any(inst.endswith(sfx) for sfx in (".DE", ".PA", ".AS", ".MI", ".MC", ".BR")):
        return "€"
    if inst.endswith(".L"):
        return "£"
    if inst.endswith(".TO") or inst.endswith(".V"):
        return "C$"
    if inst.endswith(".AX"):
        return "A$"
    if inst.endswith(".HK"):
        return "HK$"
    if inst.endswith(".T"):
        return "¥"
    return "$"


SPARKLINE_BARS = [" ", " ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
VOLUME_BARS = [" ", " ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]


def render_sparkline(values: List[float], width: int = 24) -> str:
    """Render a compact UTF-8 sparkline string for rapid trend inspection."""
    if not values:
        return "[dim]--[/dim]"
    vals = values[-width:]
    min_v = min(vals)
    max_v = max(vals)
    span = max_v - min_v
    if span <= 1e-9:
        return "[cyan]" + ("▄" * len(vals)) + "[/cyan]"

    out = []
    for v in vals:
        idx = int((v - min_v) / span * (len(SPARKLINE_BARS) - 1))
        idx = max(0, min(idx, len(SPARKLINE_BARS) - 1))
        out.append(SPARKLINE_BARS[idx])

    first_val = vals[0]
    last_val = vals[-1]
    color = "green" if last_val >= first_val else "red"
    return f"[{color}]{''.join(out)}[/{color}]"


def render_candlestick_chart(
    candles: List[dict],
    width: int = 48,
    height: int = 10,
    show_volume: bool = True,
    title: Optional[str] = None,
    currency_symbol: str = "$",
) -> str:
    """Render an institutional-grade ASCII/Unicode candlestick chart."""
    if not candles:
        return "[dim]No trade candle data available yet. Waiting for market trades...[/dim]"

    candles_per_chart = max(5, width // 3)
    vis = candles[-candles_per_chart:]

    highs = [c.get("high", c.get("close", 0.0)) for c in vis]
    lows = [c.get("low", c.get("close", 0.0)) for c in vis]
    closes = [c.get("close", 0.0) for c in vis]

    if len(vis) >= 4:
        sorted_c = sorted(closes)
        median_c = sorted_c[len(sorted_c) // 2]
        valid_lows = [lo for lo in lows if lo >= median_c * 0.90]
        valid_highs = [hi for hi in highs if hi <= median_c * 1.10]
        min_p = min(valid_lows) if valid_lows else min(lows)
        max_p = max(valid_highs) if valid_highs else max(highs)
    else:
        min_p = min(lows)
        max_p = max(highs)

    price_span = max_p - min_p
    if price_span <= 1e-6:
        padding = max_p * 0.005 if max_p > 0 else 1.0
        min_p = max(0.0, min_p - padding)
        max_p += padding
        price_span = max_p - min_p
    else:
        padding = price_span * 0.05
        min_p = max(0.0, min_p - padding)
        max_p += padding
        price_span = max_p - min_p

    lines: List[str] = []

    last_c = vis[-1]
    first_c = vis[0]
    chg_usd = last_c.get("close", 0.0) - first_c.get("open", 0.0)
    chg_pct = (
        (chg_usd / first_c.get("open", 1.0) * 100.0) if first_c.get("open") else 0.0
    )
    chg_style = "bold green" if chg_usd >= 0 else "bold red"
    chg_sign = "+" if chg_usd >= 0 else ""

    summary = (
        f"[bold white]O:[/bold white] {currency_symbol}{last_c.get('open', 0.0):,.2f}  "
        f"[bold white]H:[/bold white] {currency_symbol}{last_c.get('high', 0.0):,.2f}  "
        f"[bold white]L:[/bold white] {currency_symbol}{last_c.get('low', 0.0):,.2f}  "
        f"[bold white]C:[/bold white] {currency_symbol}{last_c.get('close', 0.0):,.2f}  "
        f"[{chg_style}]{chg_sign}{currency_symbol}{abs(chg_usd):,.2f} ({chg_sign}{chg_pct:.2f}%)[/{chg_style}]"
    )
    if title:
        lines.append(f"[bold cyan]{title}[/bold cyan]  [dim]({len(vis)} candles)[/dim]")
    lines.append(summary)
    chart_cols_len = len(vis) * 3
    lines.append("[dim]" + ("─" * (chart_cols_len + 14)) + "[/dim]")

    for r in range(height - 1, -1, -1):
        row_bot = min_p + (r / height) * price_span
        row_top = min_p + ((r + 1) / height) * price_span
        row_mid = (row_bot + row_top) / 2.0

        row_chars: List[str] = []
        for c in vis:
            op = c.get("open", 0.0)
            hi = c.get("high", 0.0)
            lo = c.get("low", 0.0)
            cl = c.get("close", 0.0)

            is_bullish = cl >= op
            b_max = max(op, cl)
            b_min = min(op, cl)
            color = "green" if is_bullish else "red"

            if hi >= row_bot and lo <= row_top:
                if b_max >= row_bot and b_min <= row_top:
                    if abs(b_max - b_min) < (price_span / height * 0.15):
                        row_chars.append(f"[{color}] ┼ [/{color}]")
                    else:
                        row_chars.append(f"[{color}] █ [/{color}]")
                elif hi > b_max and row_mid > b_max:
                    row_chars.append(f"[{color}] │ [/{color}]")
                elif lo < b_min and row_mid < b_min:
                    row_chars.append(f"[{color}] │ [/{color}]")
                else:
                    row_chars.append(f"[{color}] █ [/{color}]")
            else:
                row_chars.append("[dim] · [/dim]" if r % 2 == 0 else "   ")

        price_label = f"{currency_symbol}{row_mid:>9,.2f}"
        lines.append("".join(row_chars) + f" [dim]┤[/dim] [white]{price_label}[/white]")

    if show_volume:
        lines.append("[dim]" + ("─" * chart_cols_len) + "┴" + ("─" * 12) + "[/dim]")
        vols = [c.get("volume", 0.0) for c in vis]
        max_vol = max(vols) if vols else 1.0
        if max_vol <= 0:
            max_vol = 1.0

        vol_chars: List[str] = []
        for c in vis:
            v = c.get("volume", 0.0)
            is_bullish = c.get("close", 0.0) >= c.get("open", 0.0)
            v_col = "green" if is_bullish else "red"
            v_idx = int((v / max_vol) * (len(VOLUME_BARS) - 1))
            v_idx = max(0, min(v_idx, len(VOLUME_BARS) - 1))
            vol_chars.append(f"[{v_col}] {VOLUME_BARS[v_idx]} [/{v_col}]")

        lines.append(
            "".join(vol_chars) + f" [dim]┤[/dim] [dim]Vol (Max:{max_vol:,.0f})[/dim]"
        )

    t0 = time.strftime(
        "%H:%M:%S", time.localtime(vis[0].get("bucket_start", time.time()))
    )
    t1 = time.strftime(
        "%H:%M:%S", time.localtime(vis[-1].get("bucket_start", time.time()))
    )
    spacing = " " * max(1, (chart_cols_len - len(t0) - len(t1)))
    lines.append(f"[dim]{t0}{spacing}{t1}[/dim]")

    return "\n".join(lines)


class _DummyAnalytics:
    def __init__(self) -> None:
        self._buckets: Dict[Any, Any] = {}


class LiveTickerDashboard:
    """Headless-compatible streaming ticker processor."""

    def __init__(
        self,
        bbo_engine: Any = None,
        depth_engine: Any = None,
        candle_interval_s: float = 5.0,
    ) -> None:
        self.bbo_engine = bbo_engine
        self.depth_engine = depth_engine
        self.candle_interval_s = candle_interval_s
        self.analytics = _DummyAnalytics()
        self.event_count = 0
        self.console = Console()
        self.venue_quotes: Dict[str, Dict[str, dict]] = {}
        self.instrument_currencies: Dict[str, str] = {}
        self.session_stats: Dict[str, dict] = {}

    def update_with_event(
        self, raw: Any, ev: Optional[Any] = None, engine_ns: int = 0
    ) -> None:
        """Feed a processed market event into the live dashboard state."""
        self.event_count += 1
        p = getattr(raw, "payload", {}) or {}
        sym = (getattr(ev, "instrument_id", None) or p.get("instrument", "UNKNOWN")).upper()
        venue = (getattr(raw, "source", None) or p.get("exchange") or "UNKNOWN").upper()

        if sym not in self.venue_quotes:
            self.venue_quotes[sym] = {}
        if venue not in self.venue_quotes[sym]:
            self.venue_quotes[sym][venue] = {"bid": 0.0, "ask": 0.0, "spread": 0.0}

        bid = p.get("bid")
        ask = p.get("ask")
        if bid is not None and float(bid) > 0:
            self.venue_quotes[sym][venue]["bid"] = float(bid)
        if ask is not None and float(ask) > 0:
            self.venue_quotes[sym][venue]["ask"] = float(ask)
        cur_bid = self.venue_quotes[sym][venue]["bid"]
        cur_ask = self.venue_quotes[sym][venue]["ask"]
        if cur_bid > 0 and cur_ask > 0:
            self.venue_quotes[sym][venue]["spread"] = cur_ask - cur_bid

    def run_live_stream(
        self,
        event_stream: Any,
        pipeline: Any,
        symbols: List[str],
        single_ticker: Optional[str] = None,
        limit: Optional[int] = None,
        refresh_hz: int = 10,
    ) -> None:
        """Stream market events headlessly through the pipeline."""
        target_sym = (single_ticker or (symbols[0] if symbols else "ALL")).upper()
        self.console.print(
            f"[bold cyan]MDRAP Live Market Connector[/bold cyan] | Target: [green]{target_sym}[/green]"
        )
        for raw in event_stream:
            self.event_count += 1
            src = getattr(raw, "source", "FEED")
            if pipeline:
                pipeline.process_one(raw)
            if self.event_count <= 5 or self.event_count % 100 == 0:
                self.console.print(
                    f"  [dim]• Ingested event #{self.event_count:,} from [{src}]...[/dim]"
                )
            if limit and self.event_count >= limit:
                break

    def render_single_ticker(
        self,
        symbol: str,
        paused: bool = False,
        show_chart: bool = True,
        show_depth: bool = True,
    ) -> Panel:
        """Render a single-ticker summary panel."""
        return Panel(
            f"[bold cyan]MDRAP Single Ticker Focus: {symbol}[/bold cyan]\n"
            f"[dim]Status:[/dim] {'PAUSED' if paused else 'ACTIVE'}",
            border_style="cyan",
        )

    def render_multi_ticker_table(
        self, symbols: List[str], paused: bool = False
    ) -> Table:
        """Render a multi-ticker overview table."""
        t = Table(title="MDRAP Consolidated Multi-Ticker Cockpit", border_style="cyan")
        t.add_column("Symbol", style="bold cyan")
        t.add_column("Status", style="green")
        for s in symbols:
            t.add_row(s, "PAUSED" if paused else "ACTIVE")
        return t


# Convenience alias for legacy callers
TerminalDisplay = LiveTickerDashboard


async def run_dashboard(port: int = 9000) -> None:
    """Deprecated legacy visualizer stub."""
    print("Legacy visualizer retired. Use 'mdrap status' for operational telemetry.")
