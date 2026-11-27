"""Terminal UI dashboard for MDRAP Observer."""

from __future__ import annotations

import os
import sys
import time
from typing import Any, Optional

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


def _venue_status_style(status: str) -> str:
    """Return Rich style string distinguishing CONNECTED, STALE, and DISCONNECTED states."""
    if status == "CONNECTED":
        return "bold green"
    if status == "STALE":
        return "bold yellow"
    return "bold red"


class ObserverDashboard:
    """Terminal UI dashboard rendering venue microstructure and health metrics."""

    def __init__(self, state: ObserverState, mode: str = "TCP", exporter: Any = None):
        self.state = state
        self.mode = mode.upper()
        self.exporter = exporter
        self.start_ts = time.time()
        self.console = Console() if HAS_RICH else None

    def _get_exporter_telemetry(self) -> tuple[int, int]:
        if self.exporter is None:
            return 0, 0
        if hasattr(self.exporter, "snapshot_stats"):
            st = self.exporter.snapshot_stats()
            return int(st.get("write_errors", 0)), int(st.get("dropped_records", 0))
        return int(getattr(self.exporter, "write_errors", 0)), int(getattr(self.exporter, "dropped_records", 0))

    def render(self) -> None:
        if HAS_RICH and self.console:
            self._render_rich()
        else:
            self._render_plain()

    def _render_rich(self) -> None:
        snap = self.state.snapshot_dashboard_data()
        now = snap["now"]
        uptime_s = int(time.time() - self.start_ts)
        eps = snap["eps"]
        rss = self.state.get_rss_mb()
        stream = snap["stream"]
        venues_snap = snap["venues"]
        total_invalid = sum(v["invalid_numeric_events"] for v in venues_snap.values())
        total_stale_epoch = sum(v["stale_epoch_packets"] for v in venues_snap.values())
        total_dups = stream["duplicates"] + sum(v["duplicates"] for v in venues_snap.values())
        total_ooo = stream["out_of_order"] + sum(v["out_of_order"] for v in venues_snap.values())
        exp_errs, exp_drops = self._get_exporter_telemetry()

        # 1. Header & Summary Stats
        header_text = Text()
        header_text.append("MDRAP OBSERVER ", style="bold cyan")
        header_text.append(f"• Transport: {self.mode} ", style="bold yellow")
        header_text.append(f"• Uptime: {uptime_s}s ", style="dim")
        header_text.append(f"• Throughput: {eps:,.1f} EPS ", style="bold green")
        header_text.append(
            f"• Stream Gaps: {stream['gaps']} ",
            style="bold green" if stream["gaps"] == 0 else "bold red",
        )
        anomalies = total_invalid + total_stale_epoch + total_dups + total_ooo
        header_text.append(
            f"• Anomalies(Inv/StaleEp/Dup/OOO): {total_invalid}/{total_stale_epoch}/{total_dups}/{total_ooo} ",
            style="dim" if anomalies == 0 else "bold yellow",
        )
        if self.exporter is not None:
            header_text.append(
                f"• Export Err/Drop: {exp_errs}/{exp_drops} ",
                style="bold green" if (exp_errs == 0 and exp_drops == 0) else "bold red",
            )
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
            bid, b_sz, ask, a_sz = snap["nbbo"][asset]
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
        venue_table.add_column("Inv/StlEp", style="yellow", justify="right")
        venue_table.add_column("Age", style="dim", justify="right")

        for v in VENUES:
            vh = venues_snap[v]
            status_style = _venue_status_style(vh["status"])
            status_badge = f"[{status_style}]{vh['status']}[/{status_style}]"
            age_s = f"{now - vh['last_seen_ts']:.1f}s" if vh["last_seen_ts"] > 0 else "—"

            for i, asset in enumerate(CORE_ASSETS):
                bid_px, ask_px, spread_bps = snap["quotes"][asset][v]
                bid_str = f"${bid_px:,.2f}" if bid_px > 0 else "—"
                ask_str = f"${ask_px:,.2f}" if ask_px > 0 else "—"
                spr_str = f"{spread_bps:.2f} bps" if spread_bps > 0 else "—"

                venue_col = v if i == 0 else ""
                stat_col = status_badge if i == 0 else ""
                evt_col = f"{vh['events']:,}" if i == 0 else ""
                gap_col = f"{vh['gaps']}" if i == 0 else ""
                err_col = f"{vh['invalid_numeric_events']}/{vh['stale_epoch_packets']}" if i == 0 else ""
                age_col = age_s if i == 0 else ""

                venue_table.add_row(
                    venue_col, stat_col, asset, bid_str, ask_str, spr_str, evt_col, gap_col, err_col, age_col
                )

        # 4. Recent Trades Tape (Lee-Ready heuristic estimate)
        trade_table = Table(title="Recent Trades (Lee-Ready Heuristic)", box=ROUNDED, expand=True)
        trade_table.add_column("Time", style="dim")
        trade_table.add_column("Venue", style="cyan")
        trade_table.add_column("Asset", style="yellow")
        trade_table.add_column("Side (Est.)", justify="center")
        trade_table.add_column("Price", justify="right")
        trade_table.add_column("Size", justify="right")

        recent_prints = snap["recent_trades"]
        for tp in recent_prints:
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
        snap = self.state.snapshot_dashboard_data()
        now = snap["now"]
        uptime_s = int(time.time() - self.start_ts)
        eps = snap["eps"]
        rss = self.state.get_rss_mb()
        stream = snap["stream"]
        venues_snap = snap["venues"]
        total_invalid = sum(v["invalid_numeric_events"] for v in venues_snap.values())
        total_stale_epoch = sum(v["stale_epoch_packets"] for v in venues_snap.values())
        total_dups = stream["duplicates"] + sum(v["duplicates"] for v in venues_snap.values())
        total_ooo = stream["out_of_order"] + sum(v["out_of_order"] for v in venues_snap.values())
        exp_errs, exp_drops = self._get_exporter_telemetry()

        os.system("cls" if os.name == "nt" else "clear")
        print("=" * 80)
        print(
            f" MDRAP OBSERVER | Mode: {self.mode} | Uptime: {uptime_s}s | EPS: {eps:,.1f}"
            f" | Stream Gaps: {stream['gaps']} | RSS: {rss:.1f} MB"
        )
        print(
            f" Integrity: Inv={total_invalid} StaleEp={total_stale_epoch} Dup={total_dups} OOO={total_ooo}"
            f" | Export Err={exp_errs} Drop={exp_drops}"
        )
        print("=" * 80)
        print("\n--- CONSOLIDATED NBBO ---")
        for asset in CORE_ASSETS:
            bid, _, ask, _ = snap["nbbo"][asset]
            print(f" {asset:4s} | Bid: ${bid:,.2f} | Ask: ${ask:,.2f}")

        print("\n--- VENUES ---")
        for v in VENUES:
            vh = venues_snap[v]
            age_s = f"{now - vh['last_seen_ts']:.1f}s" if vh["last_seen_ts"] > 0 else "—"
            status_disp = "STALE (!)" if vh["status"] == "STALE" else vh["status"]
            print(
                f" {v:10s} | Status: {status_disp:12s} | Events: {vh['events']:7d}"
                f" | Gaps: {vh['gaps']:3d} | Inv/StlEp: {vh['invalid_numeric_events']}/{vh['stale_epoch_packets']}"
                f" | Age: {age_s}"
            )
        print("\n" + "=" * 80)
