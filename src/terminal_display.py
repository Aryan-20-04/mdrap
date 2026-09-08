"""
MDRAP Real-Time Terminal Visualizer & Candlestick Chart Engine.

Provides clutter-free in-place updating terminal dashboards, single-ticker
institutional focus cockpits, and visual ASCII/Unicode candlestick charts.
Never scrolls line-by-line; updates tables and graphs in place via Rich Live.
"""
from __future__ import annotations

import math
import os
import sys
import time
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from rich.console import Console, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from analytics import OHLCVAggregator
from bbo import BBOEngine, ConsolidatedBBO
from depth import ConsolidatedDepthEngine, ConsolidatedLadder
from models import CanonicalEvent, EventType, QualityStatus, RawEvent


# ---------------------------------------------------------------------------
# Cross-Platform Non-Blocking Keyboard Input (Windows msvcrt / POSIX select)
# ---------------------------------------------------------------------------

def poll_keypress() -> Optional[str]:
    """Check if a keyboard key was pressed without blocking (Windows & POSIX)."""
    if sys.platform == "win32":
        try:
            import msvcrt
            if msvcrt.kbhit():
                ch = msvcrt.getch()
                if ch in (b"\xe0", b"\x00"):  # Special key prefix (arrows, F-keys)
                    if msvcrt.kbhit():
                        msvcrt.getch()  # consume scan code
                    return None
                try:
                    return ch.decode("utf-8", errors="ignore")
                except Exception:
                    return None
        except Exception:
            return None
    else:
        try:
            import select
            r, _, _ = select.select([sys.stdin], [], [], 0)
            if r:
                return sys.stdin.read(1)
        except Exception:
            return None
    return None


# ---------------------------------------------------------------------------
# ASCII / Unicode Candlestick & Sparkline Engine
# ---------------------------------------------------------------------------

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
) -> str:
    """
    Render an institutional-grade visual ASCII/Unicode candlestick chart
    with high/low wicks, bullish/bearish candle bodies, price axis labels,
    and volume histogram.
    """
    if not candles:
        return "[dim]No trade candle data available yet. Waiting for market trades...[/dim]"

    # Take the most recent candles that fit width (3 chars per candle for clean spacing)
    candles_per_chart = max(5, width // 3)
    vis = candles[-candles_per_chart:]

    highs = [c.get("high", c.get("close", 0.0)) for c in vis]
    lows = [c.get("low", c.get("close", 0.0)) for c in vis]
    closes = [c.get("close", 0.0) for c in vis]

    # Robust outlier protection: filter out single-tick spike anomalies so normal price action fills the chart
    if len(vis) >= 4:
        sorted_c = sorted(closes)
        median_c = sorted_c[len(sorted_c) // 2]
        # Filter extreme flash crash/spike anomalies (>10% away from median close)
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

    # Chart header summary
    last_c = vis[-1]
    first_c = vis[0]
    chg_usd = last_c.get("close", 0.0) - first_c.get("open", 0.0)
    chg_pct = (chg_usd / first_c.get("open", 1.0) * 100.0) if first_c.get("open") else 0.0
    chg_style = "bold green" if chg_usd >= 0 else "bold red"
    chg_sign = "+" if chg_usd >= 0 else ""

    summary = (
        f"[bold white]O:[/bold white] ${last_c.get('open', 0.0):,.2f}  "
        f"[bold white]H:[/bold white] ${last_c.get('high', 0.0):,.2f}  "
        f"[bold white]L:[/bold white] ${last_c.get('low', 0.0):,.2f}  "
        f"[bold white]C:[/bold white] ${last_c.get('close', 0.0):,.2f}  "
        f"[{chg_style}]{chg_sign}${chg_usd:,.2f} ({chg_sign}{chg_pct:.2f}%)[/{chg_style}]"
    )
    if title:
        lines.append(f"[bold cyan]{title}[/bold cyan]  [dim]({len(vis)} candles)[/dim]")
    lines.append(summary)
    chart_cols_len = len(vis) * 3
    lines.append("[dim]" + ("─" * (chart_cols_len + 14)) + "[/dim]")

    # Render grid rows from top (max_p) down to bottom (min_p)
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

            # Check intersection with vertical price band [row_bot, row_top]
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

        price_label = f"${row_mid:>9,.2f}"
        lines.append("".join(row_chars) + f" [dim]┤[/dim] [white]{price_label}[/white]")

    # Volume histogram
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

        lines.append("".join(vol_chars) + f" [dim]┤[/dim] [dim]Vol (Max:{max_vol:,.0f})[/dim]")

    # Time indicators (First and Last candle bucket time)
    t0 = time.strftime("%H:%M:%S", time.localtime(vis[0].get("bucket_start", time.time())))
    t1 = time.strftime("%H:%M:%S", time.localtime(vis[-1].get("bucket_start", time.time())))
    spacing = " " * max(1, (chart_cols_len - len(t0) - len(t1)))
    lines.append(f"[dim]{t0}{spacing}{t1}[/dim]")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# In-Place Live Ticker Dashboard
# ---------------------------------------------------------------------------

class LiveTickerDashboard:
    """
    Renders dynamic, in-place updating terminal dashboards (Bloomberg / TradingView style).
    Prevents endless line-by-line scrolling. Supports:
    1. Single-Ticker Focus Mode (candlestick chart, NBBO, micro-price, OFI, venue breakdown).
    2. Multi-Ticker Overview Table (in-place row updates with price movement flash).
    """

    VENUE_COLORS = {
        "BINANCE": "yellow",
        "COINBASE": "blue",
        "KRAKEN": "magenta",
        "OKX": "cyan",
        "BYBIT": "bright_yellow",
        "EQUITIES": "green",
    }

    def __init__(
        self,
        bbo_engine: Optional[BBOEngine] = None,
        depth_engine: Optional[ConsolidatedDepthEngine] = None,
        candle_interval_s: float = 5.0,
    ):
        self.bbo_engine = bbo_engine or BBOEngine()
        self.depth_engine = depth_engine or ConsolidatedDepthEngine()
        self.analytics = OHLCVAggregator(interval_s=candle_interval_s)

        # In-memory tracking state
        self.last_prices: Dict[str, float] = {}          # symbol -> price
        self.price_directions: Dict[str, str] = {}       # symbol -> "UP" / "DOWN" / "FLAT"
        self.price_histories: Dict[str, List[float]] = {}# symbol -> [prices...]
        self.venue_quotes: Dict[str, Dict[str, dict]] = {}# symbol -> {venue: quote_data}
        self.session_stats: Dict[str, dict] = {}        # symbol -> {high, low, open, vol, count}
        self.event_count = 0
        self.start_time = time.time()

    def update_with_event(self, raw: RawEvent, ev: Optional[CanonicalEvent], engine_ns: int = 0) -> None:
        """Feed a processed market event into the live dashboard state."""
        self.event_count += 1
        sym = (ev.instrument_id if ev else raw.payload.get("instrument", "UNKNOWN")).upper()
        p = raw.payload

        # Update OHLCV & depth
        if ev:
            self.bbo_engine.observe(ev)
            self.depth_engine.observe(ev)
            if ev.event_type == EventType.TRADE and ev.price is not None:
                self.analytics.observe(ev)

        # Track session statistics
        if sym not in self.session_stats:
            self.session_stats[sym] = {
                "open": 0.0,
                "high": 0.0,
                "low": float("inf"),
                "volume": 0.0,
                "count": 0,
            }
        stat = self.session_stats[sym]
        stat["count"] += 1

        # Extract quote/trade price
        price = 0.0
        bid = p.get("bid", 0.0) or 0.0
        ask = p.get("ask", 0.0) or 0.0
        if "price" in p and p["price"] is not None:
            price = float(p["price"])
        elif bid > 0 and ask > 0:
            price = (bid + ask) / 2.0
        elif bid > 0:
            price = bid

        if price > 0:
            if stat["open"] == 0.0:
                stat["open"] = price
            stat["high"] = max(stat["high"], price)
            stat["low"] = min(stat["low"], price)
            if "size" in p or "quantity" in p or "qty" in p:
                q = float(p.get("size") or p.get("quantity") or p.get("qty") or 0.0)
                stat["volume"] += q

            # Movement direction
            prev_price = self.last_prices.get(sym, price)
            if price > prev_price:
                self.price_directions[sym] = "UP"
            elif price < prev_price:
                self.price_directions[sym] = "DOWN"
            else:
                self.price_directions[sym] = self.price_directions.get(sym, "FLAT")

            self.last_prices[sym] = price
            if sym not in self.price_histories:
                self.price_histories[sym] = []
            self.price_histories[sym].append(price)
            if len(self.price_histories[sym]) > 100:
                self.price_histories[sym] = self.price_histories[sym][-100:]

        # Track venue-specific quote
        if sym not in self.venue_quotes:
            self.venue_quotes[sym] = {}
        src = raw.source.upper()
        self.venue_quotes[sym][src] = {
            "bid": bid,
            "ask": ask,
            "spread": (ask - bid) if (ask > 0 and bid > 0) else 0.0,
            "bid_size": float(p.get("bid_size") or p.get("bidQty") or 1.0),
            "ask_size": float(p.get("ask_size") or p.get("askQty") or 1.0),
            "timestamp": raw.receive_timestamp,
            "engine_us": engine_ns / 1000.0,
            "quality": ev.quality_status.value if ev else "VALID",
        }

    # -----------------------------------------------------------------------
    # Rendering: Single-Ticker Focus Mode
    # -----------------------------------------------------------------------

    def render_single_ticker(
        self,
        symbol: str,
        paused: bool = False,
        show_chart: bool = True,
        show_depth: bool = False,
    ) -> RenderableType:
        """
        Build an institutional single-ticker terminal layout.
        Contains:
        1. Top: Ticker Telemetry & NBBO Header.
        2. Middle: Real-Time Candlestick Chart & Volume Histogram.
        3. Bottom: Multi-Venue Live Quotes & Depth Matrix.
        """
        sym = symbol.upper()
        sym_clean = sym.replace("-", "/")

        bbo = self.bbo_engine.current_bbo(sym) or self.bbo_engine.current_bbo(sym_clean)
        ladder = self.depth_engine.current_ladder(sym) or self.depth_engine.current_ladder(sym_clean)
        candles = self.analytics.candles_for(sym) or self.analytics.candles_for(sym_clean)
        stat = self.session_stats.get(sym, self.session_stats.get(sym_clean, {
            "open": 0.0, "high": 0.0, "low": 0.0, "volume": 0.0, "count": 0
        }))
        venues = self.venue_quotes.get(sym, self.venue_quotes.get(sym_clean, {}))

        last_px = self.last_prices.get(sym, self.last_prices.get(sym_clean, 0.0))
        direction = self.price_directions.get(sym, self.price_directions.get(sym_clean, "FLAT"))
        open_px = stat["open"] or last_px
        chg_usd = last_px - open_px if open_px else 0.0
        chg_pct = (chg_usd / open_px * 100.0) if open_px else 0.0

        # Arrow and styling
        if direction == "UP":
            arr = "▲"
            px_style = "bold green"
        elif direction == "DOWN":
            arr = "▼"
            px_style = "bold red"
        else:
            arr = "■"
            px_style = "bold yellow"

        chg_sign = "+" if chg_usd >= 0 else ""
        chg_style = "bold green" if chg_usd >= 0 else "bold red"

        # Rate calculations
        elapsed = max(0.1, time.time() - self.start_time)
        eps = self.event_count / elapsed

        # 1. Header Box
        header_text = (
            f" [bold white on blue] MDRAP Live Market Connector [/bold white on blue]  "
            f"[bold yellow]{sym}[/bold yellow] [bold cyan](FOCUS)[/bold cyan]  "
            f"[{px_style}]{arr} ${last_px:>11,.2f}[/{px_style}]  "
            f"[{chg_style}]{chg_sign}${chg_usd:,.2f} ({chg_sign}{chg_pct:.2f}%)[/{chg_style}]  "
            f"│  [dim]High:[/dim] [white]${stat['high']:,.2f}[/white]  "
            f"[dim]Low:[/dim] [white]${stat['low']:,.2f}[/white]  "
            f"[dim]Vol:[/dim] [cyan]{stat['volume']:,.1f}[/cyan]  "
            f"[dim]Events:[/dim] [bold white]{self.event_count:,}[/bold white] [dim]({eps:.1f} eps)[/dim]"
        )

        # 2. NBBO & Microstructure Strip
        if bbo and bbo.best_bid and bbo.best_ask:
            spread_bps = (bbo.spread / bbo.mid_price * 10000.0) if bbo.mid_price else 0.0
            bid_v_col = self.VENUE_COLORS.get(bbo.best_bid_source, "white")
            ask_v_col = self.VENUE_COLORS.get(bbo.best_ask_source, "white")
            arb_state = "[bold red]CROSSED (ARBITRAGE)[/bold red]" if bbo.is_crossed else "[bold green]NORMAL[/bold green]"
            micro_str = f"${ladder.micro_price:,.2f}" if ladder else f"${bbo.mid_price:,.2f}"
            ofi_val = ladder.imbalance_ratio if ladder else 0.0
            ofi_style = "green" if ofi_val > 0.1 else ("red" if ofi_val < -0.1 else "white")

            nbbo_table = Table(box=None, expand=True, pad_edge=False)
            nbbo_table.add_column("Consolidated NBBO", justify="left")
            nbbo_table.add_column("Quoted Spread", justify="center")
            nbbo_table.add_column("Micro-Price (OFI)", justify="center")
            nbbo_table.add_column("Cross-Venue Arb", justify="right")

            nbbo_table.add_row(
                f"[bold green]${bbo.best_bid:,.2f}[/bold green] @ [{bid_v_col}]{bbo.best_bid_source}[/]  "
                f"|  [bold red]${bbo.best_ask:,.2f}[/bold red] @ [{ask_v_col}]{bbo.best_ask_source}[/]",
                f"[bold white]${bbo.spread:,.2f}[/bold white] [dim]({spread_bps:.2f} bps)[/dim]",
                f"[bold cyan]{micro_str}[/bold cyan] [dim](OFI: [/dim][{ofi_style}]{ofi_val:+.2f}[/{ofi_style}][dim])[/dim]",
                arb_state,
            )
        else:
            nbbo_table = Table(box=None, expand=True)
            nbbo_table.add_column("Status")
            nbbo_table.add_row("[dim]Aggregating initial multi-venue quotes...[/dim]")

        # 3. Candlestick Chart
        chart_str = render_candlestick_chart(
            candles,
            width=56,
            height=8,
            show_volume=True,
            title=f"{sym} 5-Second Interval Candlestick Chart",
        )

        # 4. Multi-Venue Depth / Quote Breakdown
        venue_table = Table(
            title=f"Multi-Venue Top-of-Book Telemetry: {sym}",
            box=None,
            expand=True,
            show_header=True,
        )
        venue_table.add_column("Exchange", style="bold")
        venue_table.add_column("Bid Price", justify="right", style="green")
        venue_table.add_column("Ask Price", justify="right", style="red")
        venue_table.add_column("Spread", justify="right")
        venue_table.add_column("Quality", justify="center")
        venue_table.add_column("Wire Latency", justify="right", style="dim")
        venue_table.add_column("Best", justify="center")

        best_bid_val = bbo.best_bid if bbo else 0.0
        best_ask_val = bbo.best_ask if bbo else 0.0

        if venues:
            for vname, vq in venues.items():
                v_col = self.VENUE_COLORS.get(vname, "white")
                b_p = vq.get("bid", 0.0)
                a_p = vq.get("ask", 0.0)
                spr = vq.get("spread", 0.0)
                lat = vq.get("engine_us", 0.0)
                qual = vq.get("quality", "VALID")
                q_col = "green" if qual == "VALID" else "yellow"

                # Tag if this venue is National Best
                best_tag = ""
                if b_p > 0 and abs(b_p - best_bid_val) < 1e-4:
                    best_tag += "[bold green]BID★[/bold green] "
                if a_p > 0 and abs(a_p - best_ask_val) < 1e-4:
                    best_tag += "[bold red]ASK★[/bold red]"
                if not best_tag:
                    best_tag = "[dim]--[/dim]"

                venue_table.add_row(
                    f"[{v_col}]{vname:<10}[/{v_col}]",
                    f"${b_p:,.2f}" if b_p > 0 else "-",
                    f"${a_p:,.2f}" if a_p > 0 else "-",
                    f"${spr:,.2f}" if spr > 0 else "-",
                    f"[{q_col}]{qual}[/{q_col}]",
                    f"{lat:.1f}µs",
                    best_tag,
                )
        else:
            venue_table.add_row("[dim]Listening...[/dim]", "-", "-", "-", "-", "-", "-")

        # Combine into main focus cockpit
        cockpit = Table.grid(padding=(0, 0))
        if paused:
            cockpit.add_row(Panel("[bold white on red] ⏸ STREAM PAUSED / FROZEN — PRESS SPACE TO RESUME ⏸ [/bold white on red]", style="bold red", border_style="red"))
        cockpit.add_row(Panel(header_text, style="blue", border_style="cyan"))
        cockpit.add_row(Panel(nbbo_table, title="[bold]Consolidated Market Microstructure[/bold]", border_style="blue"))
        if show_chart:
            cockpit.add_row(Panel(Text.from_markup(chart_str), title="[bold]Real-Time Technical Candlestick Graph[/bold]", border_style="green"))
        if show_depth:
            depth_table = Table(box=None, expand=True, show_header=True)
            depth_table.add_column("Bid Size", justify="right", style="green")
            depth_table.add_column("Bid Price", justify="right", style="bold green")
            depth_table.add_column("Ladder", justify="center", style="dim")
            depth_table.add_column("Ask Price", justify="right", style="bold red")
            depth_table.add_column("Ask Size", justify="right", style="red")
            if ladder and (ladder.bids or ladder.asks):
                bids = ladder.bids[:5]
                asks = ladder.asks[:5]
                for i in range(max(len(bids), len(asks))):
                    b = bids[i] if i < len(bids) else None
                    a = asks[i] if i < len(asks) else None
                    b_sz = f"{b.total_size:,.2f}" if b else "-"
                    b_px = f"${b.price:,.2f}" if b else "-"
                    a_px = f"${a.price:,.2f}" if a else "-"
                    a_sz = f"{a.total_size:,.2f}" if a else "-"
                    depth_table.add_row(b_sz, b_px, f"L{i+1}", a_px, a_sz)
            else:
                depth_table.add_row("-", "-", "[dim]No L2 depth[/dim]", "-", "-")
            cockpit.add_row(Panel(depth_table, title="[bold]Consolidated Level-2 Depth Book[/bold]", border_style="cyan"))
        cockpit.add_row(Panel(venue_table, border_style="dim"))
        c_tag = "[green]ON[/green]" if show_chart else "[dim]OFF[/dim]"
        d_tag = "[green]ON[/green]" if show_depth else "[dim]OFF[/dim]"
        cockpit.add_row(Text.from_markup(
            f"[dim]Hotkeys: [bold white][q][/bold white] Quit  "
            f"[bold white][Space][/bold white] {'[bold yellow]Resume[/bold yellow]' if paused else 'Freeze'}  "
            f"[bold white][c][/bold white] Chart ({c_tag})  "
            f"[bold white][d][/bold white] Depth ({d_tag})  "
            f"[bold white][Tab][/bold white] Next Symbol[/dim]"
        ))

        return cockpit

    # -----------------------------------------------------------------------
    # Rendering: Multi-Ticker Overview Table
    # -----------------------------------------------------------------------

    def render_multi_ticker_table(self, symbols: List[str], paused: bool = False) -> RenderableType:
        """
        Build an in-place updating multi-symbol market matrix table.
        Each symbol has its dedicated row that updates in-place.
        """
        elapsed = max(0.1, time.time() - self.start_time)
        eps = self.event_count / elapsed

        table = Table(
            title=f"MDRAP Real-Time Multi-Venue Market Stream ({self.event_count:,} events | {eps:.1f} eps)",
            expand=True,
            show_lines=True,
        )
        table.add_column("Symbol", style="bold white", width=10)
        table.add_column("Last Price", justify="right", width=12)
        table.add_column("Net Chg", justify="right", width=9)
        table.add_column("Best Bid", justify="right", style="green", width=12)
        table.add_column("Best Ask", justify="right", style="red", width=12)
        table.add_column("Spread", justify="right", width=10)
        table.add_column("Micro-Price", justify="right", style="cyan", width=12)
        table.add_column("OFI", justify="center", width=8)
        table.add_column("CVD", justify="right", width=9)
        table.add_column("Trend", justify="center", width=11)
        table.add_column("Venues", justify="left", width=16)
        table.add_column("Status", justify="center", width=9)

        for sym in symbols:
            s_clean = sym.replace("-", "/")
            bbo = self.bbo_engine.current_bbo(sym) or self.bbo_engine.current_bbo(s_clean)
            ladder = self.depth_engine.current_ladder(sym) or self.depth_engine.current_ladder(s_clean)
            stat = self.session_stats.get(sym, self.session_stats.get(s_clean, {
                "open": 0.0, "high": 0.0, "low": 0.0, "volume": 0.0, "count": 0
            }))
            venues = self.venue_quotes.get(sym, self.venue_quotes.get(s_clean, {}))

            last_px = self.last_prices.get(sym, self.last_prices.get(s_clean, 0.0))
            direction = self.price_directions.get(sym, self.price_directions.get(s_clean, "FLAT"))
            open_px = stat["open"] or last_px
            chg_usd = last_px - open_px if open_px else 0.0
            chg_pct = (chg_usd / open_px * 100.0) if open_px else 0.0

            if direction == "UP":
                arr = "▲"
                px_style = "bold green"
            elif direction == "DOWN":
                arr = "▼"
                px_style = "bold red"
            else:
                arr = "■"
                px_style = "bold yellow"

            chg_sign = "+" if chg_usd >= 0 else ""
            chg_style = "green" if chg_usd >= 0 else "red"

            # BBO metrics
            if bbo and bbo.best_bid and bbo.best_ask:
                bid_str = f"${bbo.best_bid:,.2f}"
                ask_str = f"${bbo.best_ask:,.2f}"
                spr_str = f"${bbo.spread:,.2f}"
                arb_str = "[bold red]CROSS[/bold red]" if bbo.is_crossed else "[green]VALID[/green]"
            else:
                bid_str = "-"
                ask_str = "-"
                spr_str = "-"
                arb_str = "[dim]PENDING[/dim]"

            micro_str = f"${ladder.micro_price:,.2f}" if ladder else (
                f"${bbo.mid_price:,.2f}" if (bbo and bbo.mid_price) else "-"
            )

            ofi_val = ladder.ofi if (ladder and ladder.ofi != 0.0) else (ladder.imbalance_ratio if ladder else 0.0)
            ofi_style = "green" if ofi_val > 0.05 else ("red" if ofi_val < -0.05 else "dim")
            ofi_str = f"[{ofi_style}]{ofi_val:+.2f}[/{ofi_style}]"

            cvd_val = ladder.cvd if ladder else 0.0
            cvd_style = "bold green" if cvd_val > 0 else ("bold red" if cvd_val < 0 else "dim")
            cvd_str = f"[{cvd_style}]{cvd_val:+,.0f}[/{cvd_style}]" if cvd_val != 0 else "[dim]0[/dim]"

            # Sparkline
            spark = render_sparkline(self.price_histories.get(sym, self.price_histories.get(s_clean, [])), width=10)

            # Active venue badges
            v_badges = []
            for v in sorted(venues.keys()):
                v_col = self.VENUE_COLORS.get(v, "white")
                v_badges.append(f"[{v_col}]{v[:3]}[/{v_col}]")
            venue_str = " ".join(v_badges) if v_badges else "[dim]--[/dim]"

            table.add_row(
                sym,
                f"[{px_style}]{arr} ${last_px:,.2f}[/{px_style}]" if last_px > 0 else "[dim]Waiting[/dim]",
                f"[{chg_style}]{chg_sign}{chg_pct:.1f}%[/{chg_style}]",
                bid_str,
                ask_str,
                spr_str,
                micro_str,
                ofi_str,
                cvd_str,
                spark,
                venue_str,
                arb_str,
            )

        footer = Text.from_markup(
            f"\n[dim]Hotkeys: [bold white][q][/bold white] Quit  "
            f"[bold white][Space][/bold white] {'[bold yellow]Resume[/bold yellow]' if paused else 'Freeze'}  "
            f"[bold white][1-9][/bold white] Focus Symbol | "
            f"Run [bold cyan]mdrap live <SYM>[/bold cyan] for Single-Ticker Candlestick Focus[/dim]"
        )
        grid = Table.grid()
        if paused:
            grid.add_row(Panel("[bold white on red] ⏸ STREAM PAUSED / FROZEN — PRESS SPACE TO RESUME ⏸ [/bold white on red]", style="bold red", border_style="red"))
        grid.add_row(table)
        grid.add_row(footer)
        return grid

    # -----------------------------------------------------------------------
    # Stream Runner Loop
    # -----------------------------------------------------------------------

    def run_watchlist_stream(
        self,
        event_stream: Generator[RawEvent, None, None],
        pipeline: Any,
        symbols: List[str],
        limit: Optional[int] = None,
    ) -> None:
        """
        Execute live multi-ticker portfolio watchlist stream.
        Renders an in-place updating matrix of all tracked symbols with OFI, CVD, and venue health.
        """
        return self.run_live_stream(
            event_stream=event_stream,
            pipeline=pipeline,
            symbols=symbols,
            single_ticker=None,
            limit=limit,
        )

    def run_live_stream(
        self,
        event_stream: Generator[RawEvent, None, None],
        pipeline: Any,
        symbols: List[str],
        single_ticker: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> None:
        """
        Execute the live ingestion stream with persistent, in-place terminal updates.
        Never prints new scrolling lines. Supports interactive hotkeys (Space freeze, q quit, c/d toggles).
        """
        console = Console()
        is_single = bool(single_ticker) or (len(symbols) == 1 and symbols[0].upper() not in ("ALL", "*"))
        target_sym = single_ticker or (symbols[0] if is_single else None)

        paused = False
        show_chart = True
        show_depth = False

        initial_render = (
            self.render_single_ticker(target_sym, paused=paused, show_chart=show_chart, show_depth=show_depth) if is_single
            else self.render_multi_ticker_table(symbols, paused=paused)
        )

        with Live(initial_render, console=console, refresh_per_second=8, transient=False) as live:
            count = 0
            stream_iter = iter(event_stream)
            while True:
                # 1. Non-blocking keypress check
                key = poll_keypress()
                if key:
                    if key in ("q", "Q", "\x1b"):  # 'q' or Escape
                        break
                    elif key == " ":
                        paused = not paused
                        if is_single and target_sym:
                            live.update(self.render_single_ticker(target_sym, paused=paused, show_chart=show_chart, show_depth=show_depth))
                        else:
                            live.update(self.render_multi_ticker_table(symbols, paused=paused))
                    elif key in ("c", "C"):
                        show_chart = not show_chart
                        if is_single and target_sym:
                            live.update(self.render_single_ticker(target_sym, paused=paused, show_chart=show_chart, show_depth=show_depth))
                    elif key in ("d", "D"):
                        show_depth = not show_depth
                        if is_single and target_sym:
                            live.update(self.render_single_ticker(target_sym, paused=paused, show_chart=show_chart, show_depth=show_depth))
                    elif key == "\t":
                        if len(symbols) > 1:
                            if target_sym in symbols:
                                idx = (symbols.index(target_sym) + 1) % len(symbols)
                                target_sym = symbols[idx]
                            else:
                                target_sym = symbols[0]
                            is_single = True
                            live.update(self.render_single_ticker(target_sym, paused=paused, show_chart=show_chart, show_depth=show_depth))
                    elif key.isdigit() and 1 <= int(key) <= len(symbols):
                        idx = int(key) - 1
                        target_sym = symbols[idx]
                        is_single = True
                        live.update(self.render_single_ticker(target_sym, paused=paused, show_chart=show_chart, show_depth=show_depth))

                if paused:
                    time.sleep(0.05)
                    continue

                # 2. Ingest next event
                try:
                    raw = next(stream_iter)
                except (StopIteration, GeneratorExit):
                    break
                except Exception:
                    break

                t0 = time.perf_counter_ns()
                ev = pipeline.process_one(raw)
                engine_ns = time.perf_counter_ns() - t0

                self.update_with_event(raw, ev, engine_ns)
                count += 1

                # Update terminal in place
                if is_single and target_sym:
                    live.update(self.render_single_ticker(target_sym, paused=paused, show_chart=show_chart, show_depth=show_depth))
                else:
                    live.update(self.render_multi_ticker_table(symbols, paused=paused))

                if limit and count >= limit:
                    break


# Convenience alias
TerminalDisplay = LiveTickerDashboard
