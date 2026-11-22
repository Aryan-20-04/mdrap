"""Terminal UI dashboard for MDRAP Observer."""

from __future__ import annotations

import os
import sys
import time
from typing import Optional

from .state import CORE_ASSETS, VENUES, ObserverState

try:
    from rich.box import ROUNDED
    from rich.console import Console
    from rich.layout import Layout
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    HAS_RICH = True
except ImportError:
    HAS_RICH = False


class ObserverDashboard:
    """Terminal UI dashboard rendering venue microstructure and health metrics."""

    def __init__(self, state: ObserverState, mode: str = "TCP"):
        self.state = state
        self.mode = mode.upper()
        self.start_ts = time.time()
        self.console = Console() if HAS_RICH else None

    def render(self) -> None:
        if HAS_RICH and self.console:
            self._render_rich()
        else:
            self._render_plain()

    def _render_rich(self) -> None:
        uptime_s = int(time.time() - self.start_ts)
        eps = self.state.get_throughput_eps()
        rss = self.state.get_rss_mb()

        # 1. Header & Summary Stats
        header_text = Text()
        header_text.append("MDRAP OBSERVER ", style="bold cyan")
        header_text.append(f"• Transport: {self.mode} ", style="bold yellow")
        header_text.append(f"• Uptime: {uptime_s}s ", style="dim")
        header_text.append(f"• Throughput: {eps:,.1f} EPS ", style="bold green")
        header_text.append(f"• Stream Gaps: {self.state.stream_health.gaps} ", style="bold green" if self.state.stream_health.gaps == 0 else "bold red")
        header_text.append(f"• Memory RSS: {rss:.1f} MiB", style="bold magenta")

        summary_panel = Panel(header_text, box=ROUNDED, border_style="cyan")

        # 2. Consolidated NBBO Table
        nbbo_table = Table(title="Consolidated Best Bid & Offer (NBBO)", box=ROUNDED, expand=True)
        nbbo_table.add_column("Asset", style="bold yellow", justify="left")
        nbbo_table.add_column("Best Bid", style="bold green", justify="right")
        nbbo_table.add_column("Bid Size", style="dim", justify="right")
        nbbo_table.add_column("Best Ask", style="bold red", justify="right")
        nbbo_table.add_column("Ask Size", style="dim", justify="right")
        nbbo_table.add_column("Mid Price", style="bold white", justify="right")
        nbbo_table.add_column("Spread (bps)", style="bold cyan", justify="right")

        for asset in CORE_ASSETS:
            bid, b_sz, ask, a_sz = self.state.get_consolidated_nbbo(asset)
            mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else max(bid, ask)
            spread_bps = ((ask - bid) / mid * 10000.0) if bid > 0 and ask >= bid else 0.0
            nbbo_table.add_row(
                asset,
                f"${bid:,.2f}" if bid > 0 else "—",
                f"{b_sz:.4f}" if b_sz > 0 else "—",
                f"${ask:,.2f}" if ask > 0 else "—",
                f"{a_sz:.4f}" if a_sz > 0 else "—",
                f"${mid:,.2f}" if mid > 0 else "—",
                f"{spread_bps:.2f} bps" if spread_bps > 0 else "—",
            )

        # 3. 5-Venue Matrix Table
        venue_table = Table(title="5-Venue Feed Integrity & Depth Matrix", box=ROUNDED, expand=True)
        venue_table.add_column("Venue", style="bold cyan")
        venue_table.add_column("Status", justify="center")
        venue_table.add_column("Asset", justify="center")
        venue_table.add_column("Bid", style="green", justify="right")
        venue_table.add_column("Ask", style="red", justify="right")
        venue_table.add_column("Spread", justify="right")
        venue_table.add_column("Events", style="dim", justify="right")
        venue_table.add_column("Gaps", style="yellow", justify="right")
        venue_table.add_column("Reconnects", style="dim", justify="right")
        venue_table.add_column("Age", style="dim", justify="right")

        now = time.time()
        for v in VENUES:
            vh = self.state.health[v]
            status_style = "bold green" if vh.status == "CONNECTED" else "bold red"
            status_badge = f"[{status_style}]{vh.status}[/{status_style}]"
            age_s = f"{now - vh.last_seen_ts:.1f}s" if vh.last_seen_ts > 0 else "—"

            for i, asset in enumerate(CORE_ASSETS):
                q = self.state.quotes[asset][v]
                bid_str = f"${q.bid_price:,.2f}" if q.bid_price > 0 else "—"
                ask_str = f"${q.ask_price:,.2f}" if q.ask_price > 0 else "—"
                spr_str = f"{q.spread_bps:.2f} bps" if q.spread_bps > 0 else "—"

                venue_col = v if i == 0 else ""
                stat_col = status_badge if i == 0 else ""
                evt_col = f"{vh.events_received:,}" if i == 0 else ""
                gap_col = f"{vh.gaps}" if i == 0 else ""
                rec_col = f"{vh.reconnects}" if i == 0 else ""
                age_col = age_s if i == 0 else ""

                venue_table.add_row(
                    venue_col, stat_col, asset, bid_str, ask_str, spr_str, evt_col, gap_col, rec_col, age_col
                )

        # 4. Recent Trades Tape
        trade_table = Table(title="Recent Verified Trades", box=ROUNDED, expand=True)
        trade_table.add_column("Time", style="dim")
        trade_table.add_column("Venue", style="cyan")
        trade_table.add_column("Asset", style="yellow")
        trade_table.add_column("Side", justify="center")
        trade_table.add_column("Price", justify="right")
        trade_table.add_column("Size", justify="right")

        recent_prints = []
        for asset in CORE_ASSETS:
            recent_prints.extend(list(self.state.trades[asset])[:5])
        recent_prints.sort(key=lambda t: t.timestamp, reverse=True)

        for tp in recent_prints[:6]:
            side_color = "bold green" if tp.side == "BUY" else "bold red"
            time_str = time.strftime("%H:%M:%S", time.localtime(tp.timestamp))
            trade_table.add_row(
                time_str,
                tp.venue,
                tp.symbol,
                f"[{side_color}]{tp.side}[/{side_color}]",
                f"${tp.price:,.2f}",
                f"{tp.size:.4f}",
            )

        if not recent_prints:
            trade_table.add_row("—", "—", "—", "—", "Awaiting trades...", "—")

        # Clear and print
        os.system("cls" if os.name == "nt" else "clear")
        self.console.print(summary_panel)
        self.console.print(nbbo_table)
        self.console.print(venue_table)
        self.console.print(trade_table)

    def _render_plain(self) -> None:
        """ANSI fallback for non-rich environments."""
        os.system("cls" if os.name == "nt" else "clear")
        uptime_s = int(time.time() - self.start_ts)
        eps = self.state.get_throughput_eps()
        rss = self.state.get_rss_mb()

        print(f"================================================================================")
        print(f" MDRAP OBSERVER | Mode: {self.mode} | Uptime: {uptime_s}s | EPS: {eps:,.1f} | RSS: {rss:.1f} MB")
        print(f"================================================================================")
        print("\n--- CONSOLIDATED NBBO ---")
        for asset in CORE_ASSETS:
            bid, _, ask, _ = self.state.get_consolidated_nbbo(asset)
            print(f" {asset:4s} | Bid: ${bid:,.2f} | Ask: ${ask:,.2f}")

        print("\n--- VENUES ---")
        for v in VENUES:
            vh = self.state.health[v]
            print(f" {v:10s} | Status: {vh.status:12s} | Events: {vh.events_received:7d} | Gaps: {vh.gaps:3d}")
        print("\n" + "=" * 80)
