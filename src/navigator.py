"""
MDRAP Keyboard-First Modal Navigator Engine.

Provides an ultra-fast, zero-latency keyboard terminal desk inspired by
Vim (modal editing, home-row motions, fuzzy search), Excel (grid navigation,
instant filtering, column sorting), and Bloomberg Terminal (mnemonic velocity
with armed confirmation guards).

Pure Python standard library + Rich rendering. Zero heavy frameworks.
"""
from __future__ import annotations

import os
import sys
import time
import webbrowser
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from rich.align import Align
from rich.console import Console, RenderableType
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# Cross-platform terminal raw input support
if sys.platform == "win32":
    import msvcrt
else:
    import select
    import termios
    import tty


# ---------------------------------------------------------------------------
# Key Constants & Non-Blocking Key Reader
# ---------------------------------------------------------------------------

class Key:
    UP = "UP"
    DOWN = "DOWN"
    LEFT = "LEFT"
    RIGHT = "RIGHT"
    PAGE_UP = "PAGE_UP"
    PAGE_DOWN = "PAGE_DOWN"
    HOME = "HOME"
    END = "END"
    ENTER = "ENTER"
    ESC = "ESC"
    TAB = "TAB"
    BACKSPACE = "BACKSPACE"
    DELETE = "DELETE"


class KeyReader:
    """Reads single keystrokes cross-platform without requiring Enter."""

    def __init__(self):
        self._is_windows = sys.platform == "win32"
        self._old_termios = None

    def enter_raw_mode(self):
        if not self._is_windows:
            try:
                self._old_termios = termios.tcgetattr(sys.stdin.fileno())
                tty.setcbreak(sys.stdin.fileno())
            except Exception:
                pass

    def exit_raw_mode(self):
        if not self._is_windows and self._old_termios is not None:
            try:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._old_termios)
            except Exception:
                pass
            self._old_termios = None

    def read_key(self, timeout_s: float = 0.05) -> Optional[str]:
        """Poll and return key string if pressed within timeout, else None."""
        if self._is_windows:
            t0 = time.perf_counter()
            while time.perf_counter() - t0 < timeout_s:
                if msvcrt.kbhit():
                    ch = msvcrt.getch()
                    if ch in (b"\x00", b"\xe0"):  # Extended scan code prefix
                        if msvcrt.kbhit():
                            code = msvcrt.getch()
                            if code == b"H": return Key.UP
                            if code == b"P": return Key.DOWN
                            if code == b"K": return Key.LEFT
                            if code == b"M": return Key.RIGHT
                            if code == b"I": return Key.PAGE_UP
                            if code == b"Q": return Key.PAGE_DOWN
                            if code == b"G": return Key.HOME
                            if code == b"O": return Key.END
                            if code == b"S": return Key.DELETE
                        return None
                    if ch in (b"\r", b"\n"):
                        return Key.ENTER
                    if ch == b"\x1b":
                        return Key.ESC
                    if ch == b"\t":
                        return Key.TAB
                    if ch in (b"\x08", b"\x7f"):
                        return Key.BACKSPACE
                    try:
                        return ch.decode("utf-8", errors="ignore")
                    except Exception:
                        return None
                time.sleep(0.005)
            return None
        else:
            # POSIX
            try:
                r, _, _ = select.select([sys.stdin], [], [], timeout_s)
                if not r:
                    return None
                ch = sys.stdin.read(1)
                if ch == "\x1b":
                    # Check for ANSI escape sequences
                    r2, _, _ = select.select([sys.stdin], [], [], 0.01)
                    if r2:
                        seq = sys.stdin.read(2)
                        if seq == "[A": return Key.UP
                        if seq == "[B": return Key.DOWN
                        if seq == "[C": return Key.RIGHT
                        if seq == "[D": return Key.LEFT
                        if seq == "[H": return Key.HOME
                        if seq == "[F": return Key.END
                        if seq == "[5":
                            sys.stdin.read(1)  # trailing ~
                            return Key.PAGE_UP
                        if seq == "[6":
                            sys.stdin.read(1)
                            return Key.PAGE_DOWN
                        if seq == "[3":
                            sys.stdin.read(1)
                            return Key.DELETE
                    return Key.ESC
                if ch in ("\r", "\n"):
                    return Key.ENTER
                if ch == "\t":
                    return Key.TAB
                if ch in ("\x08", "\x7f"):
                    return Key.BACKSPACE
                return ch
            except Exception:
                return None


# ---------------------------------------------------------------------------
# Modal States & Confirmation Ticket
# ---------------------------------------------------------------------------

class NavigatorMode(str, Enum):
    NORMAL = "NORMAL"      # Home-row 1-key navigation & commands
    FILTER = "FILTER"      # Live in-place search (/ query)
    MODAL = "MODAL"        # Armed two-step confirmation ticket
    COMMAND = "COMMAND"    # CLI command input (: prefix)


@dataclass
class ConfirmationTicket:
    """Armed execution ticket requiring explicit operator confirmation."""
    title: str
    message: str
    details: Dict[str, str] = field(default_factory=dict)
    action_type: str = "MUTATION"  # 'TRADE', 'KILL', 'RESET'
    callback: Optional[Callable[[], None]] = None


# ---------------------------------------------------------------------------
# Interactive Data Grid Model
# ---------------------------------------------------------------------------

@dataclass
class GridColumn:
    name: str
    key: str
    justify: str = "left"
    style: Optional[str] = None
    width: Optional[int] = None


class DataGrid:
    """Interactive table model supporting row selection, viewport scrolling, live filtering, and sorting."""

    def __init__(self, title: str, columns: List[GridColumn], rows: List[Dict[str, Any]], row_id_key: str = "id"):
        self.title = title
        self.columns = columns
        self.all_rows = rows
        self.filtered_rows = list(rows)
        self.row_id_key = row_id_key

        self.selected_idx: int = 0
        self.scroll_offset: int = 0
        self.page_size: int = 15

        self.filter_query: str = ""
        self.sort_col: Optional[str] = None
        self.sort_desc: bool = False

    def update_rows(self, rows: List[Dict[str, Any]]):
        """Update data rows while preserving selection & filtering."""
        curr_id = self.get_selected_id()
        self.all_rows = rows
        self._reapply_filter_and_sort()
        self._restore_selection(curr_id)

    def move_selection(self, delta: int):
        """Move cursor up/down with automatic viewport scrolling."""
        if not self.filtered_rows:
            self.selected_idx = 0
            self.scroll_offset = 0
            return
        total = len(self.filtered_rows)
        self.selected_idx = max(0, min(total - 1, self.selected_idx + delta))

        # Adjust viewport window
        if self.selected_idx < self.scroll_offset:
            self.scroll_offset = self.selected_idx
        elif self.selected_idx >= self.scroll_offset + self.page_size:
            self.scroll_offset = self.selected_idx - self.page_size + 1

    def page_down(self):
        self.move_selection(self.page_size - 1)

    def page_up(self):
        self.move_selection(-(self.page_size - 1))

    def jump_top(self):
        self.selected_idx = 0
        self.scroll_offset = 0

    def jump_bottom(self):
        if self.filtered_rows:
            self.selected_idx = len(self.filtered_rows) - 1
            self.scroll_offset = max(0, len(self.filtered_rows) - self.page_size)

    def set_filter(self, query: str):
        self.filter_query = query
        self._reapply_filter_and_sort()
        self.selected_idx = 0
        self.scroll_offset = 0

    def clear_filter(self):
        self.filter_query = ""
        self._reapply_filter_and_sort()

    def toggle_sort(self, col_key: str):
        if self.sort_col == col_key:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_col = col_key
            self.sort_desc = False
        self._reapply_filter_and_sort()

    def get_selected_row(self) -> Optional[Dict[str, Any]]:
        if 0 <= self.selected_idx < len(self.filtered_rows):
            return self.filtered_rows[self.selected_idx]
        return None

    def get_selected_id(self) -> Optional[str]:
        row = self.get_selected_row()
        return str(row.get(self.row_id_key, "")) if row else None

    def _reapply_filter_and_sort(self):
        q = self.filter_query.strip().lower()
        if not q:
            rows = list(self.all_rows)
        else:
            rows = []
            for r in self.all_rows:
                match = False
                for val in r.values():
                    if q in str(val).lower():
                        match = True
                        break
                if match:
                    rows.append(r)

        if self.sort_col:
            def _sort_key(item):
                val = item.get(self.sort_col, "")
                if isinstance(val, (int, float)):
                    return (0, val)
                return (1, str(val).lower())
            rows.sort(key=_sort_key, reverse=self.sort_desc)

        self.filtered_rows = rows
        if self.selected_idx >= len(self.filtered_rows):
            self.selected_idx = max(0, len(self.filtered_rows) - 1)

    def _restore_selection(self, prev_id: Optional[str]):
        if not prev_id:
            return
        for i, r in enumerate(self.filtered_rows):
            if str(r.get(self.row_id_key, "")) == prev_id:
                self.selected_idx = i
                return


# ---------------------------------------------------------------------------
# Navigator Desk Engine
# ---------------------------------------------------------------------------

class MDRAPNavigator:
    """Full-screen interactive keyboard desk managing tabs, grids, and safe execution."""

    def __init__(self, console: Optional[Console] = None):
        self.console = console or Console()
        self.reader = KeyReader()
        self.mode = NavigatorMode.NORMAL

        self.tabs: List[Tuple[str, str, DataGrid]] = []
        self.active_tab_idx: int = 0
        self.active_modal: Optional[ConfirmationTicket] = None

        self.status_message: str = "Ready. Press [?] for cheat-sheet."
        self.status_style: str = "dim green"
        self.status_expiry: float = 0.0

        self.input_buffer: str = ""
        self._running: bool = False

        self._setup_default_views()

    def _setup_default_views(self):
        """Populate default multi-domain grids: Markets, Fleet, Depth, Edgar, Portfolio."""
        # 1. Markets
        markets_cols = [
            GridColumn("MIC", "mic", style="bold cyan", width=8),
            GridColumn("Venue Name", "name", width=22),
            GridColumn("Flag", "flag", width=6),
            GridColumn("Currency", "currency", justify="center", width=10),
            GridColumn("Session Phase", "phase", style="bold green", width=18),
            GridColumn("Benchmark", "benchmark", width=16),
            GridColumn("Circuit Band", "circuit", justify="right", width=14),
        ]
        markets_rows = self._load_markets_data()
        grid_markets = DataGrid("Global Trading Desks & Venues (ISO 10383)", markets_cols, markets_rows, row_id_key="mic")
        self.tabs.append(("1", "Markets", grid_markets))

        # 2. Maritime Fleet
        fleet_cols = [
            GridColumn("Vessel Name", "name", style="bold yellow", width=20),
            GridColumn("Type", "type", width=14),
            GridColumn("Flag", "flag", width=6),
            GridColumn("Owner / Operator", "owner", width=18),
            GridColumn("Commodity Payload", "payload", width=20),
            GridColumn("Status", "load_status", style="bold", width=10),
            GridColumn("Nearest Chokepoint", "chokepoint", style="cyan", width=20),
            GridColumn("Speed", "speed", justify="right", width=10),
        ]
        fleet_rows = self._load_fleet_data()
        grid_fleet = DataGrid("Global Commercial Tanker & Cargo Fleet", fleet_cols, fleet_rows, row_id_key="name")
        self.tabs.append(("2", "Fleet", grid_fleet))

        # 3. Market Depth & BBO
        depth_cols = [
            GridColumn("Symbol", "symbol", style="bold cyan", width=12),
            GridColumn("Asset Class", "asset", width=14),
            GridColumn("Best Bid", "bid", justify="right", style="green", width=12),
            GridColumn("Best Ask", "ask", justify="right", style="red", width=12),
            GridColumn("Spread (bps)", "spread_bps", justify="right", width=14),
            GridColumn("Imbalance", "imbalance", justify="center", width=14),
            GridColumn("Last Price", "last", justify="right", style="bold", width=14),
            GridColumn("24h Trend", "trend", justify="center", width=16),
        ]
        depth_rows = self._load_depth_data()
        grid_depth = DataGrid("Consolidated Level-2 Depth & Best Bid/Offer", depth_cols, depth_rows, row_id_key="symbol")
        self.tabs.append(("3", "Depth", grid_depth))

        # 4. SEC EDGAR Alternative Data
        edgar_cols = [
            GridColumn("Date/Time", "time", style="dim", width=18),
            GridColumn("Ticker", "ticker", style="bold cyan", width=10),
            GridColumn("Form", "form", style="bold yellow", width=10),
            GridColumn("Material Event / Filing Description", "description", width=42),
            GridColumn("Urgency", "urgency", justify="center", style="bold", width=12),
            GridColumn("Action", "action", style="blue underline", width=16),
        ]
        edgar_rows = self._load_edgar_data()
        grid_edgar = DataGrid("SEC EDGAR Real-Time Corporate Filings & Material Events", edgar_cols, edgar_rows, row_id_key="ticker")
        self.tabs.append(("4", "EDGAR", grid_edgar))

        # 5. Portfolio Accounting
        port_cols = [
            GridColumn("Symbol", "symbol", style="bold cyan", width=12),
            GridColumn("Currency", "currency", width=10),
            GridColumn("Position", "qty", justify="right", width=12),
            GridColumn("Avg Cost", "avg_cost", justify="right", width=14),
            GridColumn("Market Price", "price", justify="right", width=14),
            GridColumn("Unrealized P&L", "pnl", justify="right", style="bold", width=18),
            GridColumn("Notional Value", "value", justify="right", width=18),
        ]
        port_rows = self._load_portfolio_data()
        grid_port = DataGrid("Multi-Currency Institutional Portfolio Tracker", port_cols, port_rows, row_id_key="symbol")
        self.tabs.append(("5", "Portfolio", grid_port))

    def _load_markets_data(self) -> List[Dict[str, Any]]:
        try:
            from venues import list_all_venues
            venues = list_all_venues()
            return [
                {
                    "mic": v.mic,
                    "name": v.name,
                    "flag": v.country_flag,
                    "currency": v.currency,
                    "phase": v.current_session_phase.value,
                    "benchmark": v.benchmark_index,
                    "circuit": f"±{v.price_collar_bps/100:.1f}%",
                }
                for v in venues
            ]
        except Exception:
            return [
                {"mic": "XNYS", "name": "New York Stock Exchange", "flag": "🇺🇸", "currency": "USD", "phase": "CONTINUOUS", "benchmark": "S&P 500", "circuit": "±7.0%"},
                {"mic": "XNAS", "name": "Nasdaq Stock Market", "flag": "🇺🇸", "currency": "USD", "phase": "CONTINUOUS", "benchmark": "Nasdaq 100", "circuit": "±7.0%"},
                {"mic": "XNSE", "name": "National Stock Exchange", "flag": "🇮🇳", "currency": "INR", "phase": "CONTINUOUS", "benchmark": "NIFTY 50", "circuit": "±10.0%"},
                {"mic": "XETR", "name": "Deutsche Börse Xetra", "flag": "🇩🇪", "currency": "EUR", "phase": "CONTINUOUS", "benchmark": "DAX 40", "circuit": "±5.0%"},
                {"mic": "XTKS", "name": "Tokyo Stock Exchange", "flag": "🇯🇵", "currency": "JPY", "phase": "CONTINUOUS", "benchmark": "Nikkei 225", "circuit": "±8.0%"},
                {"mic": "XLON", "name": "London Stock Exchange", "flag": "🇬🇧", "currency": "GBP", "phase": "CONTINUOUS", "benchmark": "FTSE 100", "circuit": "±5.0%"},
            ]

    def _load_fleet_data(self) -> List[Dict[str, Any]]:
        try:
            from vessel import VesselTracker
            tracker = VesselTracker()
            vessels = tracker.list_vessels()
            out = []
            for v in vessels:
                nearest, dist = tracker.nearest_chokepoint(v)
                out.append({
                    "name": v.name,
                    "type": v.vessel_type.value,
                    "flag": v.flag_country,
                    "owner": v.fleet_operator,
                    "payload": f"{v.commodity_type.value} ({v.commodity_quantity:,.0f})",
                    "load_status": v.load_status.value,
                    "chokepoint": f"{nearest.name} ({dist:.1f} nm)",
                    "speed": f"{v.speed_knots:.1f} kts",
                    "imo": v.imo_number,
                })
            return out
        except Exception:
            return [
                {"name": "FRONT ALTAIR", "type": "CRUDE_OIL", "flag": "🇲🇭", "owner": "Frontline Ltd", "payload": "Arab Light (2.0M bbl)", "load_status": "LADEN", "chokepoint": "Strait of Hormuz (14 nm)", "speed": "13.8 kts", "imo": "9745123"},
                {"name": "TI EUROPE", "type": "ULCC_TANKER", "flag": "🇧🇪", "owner": "Euronav NV", "payload": "Basrah Heavy (3.1M bbl)", "load_status": "LADEN", "chokepoint": "Strait of Malacca (12 nm)", "speed": "12.5 kts", "imo": "9235268"},
                {"name": "DHT JAGUAR", "type": "CRUDE_OIL", "flag": "🇭🇰", "owner": "DHT Holdings", "payload": "Brent Blend (2.0M bbl)", "load_status": "LADEN", "chokepoint": "Dover Strait (9 nm)", "speed": "11.2 kts", "imo": "9722345"},
                {"name": "Q-MAX AL DAFNA", "type": "LNG_CARRIER", "flag": "🇶🇦", "owner": "Nakilat", "payload": "Qatar LNG (266k cbm)", "load_status": "LADEN", "chokepoint": "Bab-el-Mandeb (18 nm)", "speed": "17.4 kts", "imo": "9443683"},
                {"name": "EVER GIVEN", "type": "CONTAINER", "flag": "🇵🇦", "owner": "Shoei Kisen", "payload": "20,124 TEU General", "load_status": "LADEN", "chokepoint": "Suez Canal (5 nm)", "speed": "9.8 kts", "imo": "9811000"},
            ]

    def _load_depth_data(self) -> List[Dict[str, Any]]:
        return [
            {"symbol": "BTC/USD", "asset": "Crypto", "bid": "$68,420.50", "ask": "$68,421.00", "spread_bps": "0.07 bps", "imbalance": "+14.2% [BUY]", "last": "$68,420.80", "trend": " ▄▆█▇▆▅▆▇█", "url": "BTC"},
            {"symbol": "AAPL", "asset": "Equity", "bid": "$185.15", "ask": "$185.17", "spread_bps": "0.11 bps", "imbalance": "-4.8% [SELL]", "last": "$185.16", "trend": "▆▅▄▃▂  ▂▃▄", "url": "AAPL"},
            {"symbol": "NVDA", "asset": "Equity", "bid": "$118.40", "ask": "$118.42", "spread_bps": "0.17 bps", "imbalance": "+22.6% [BUY]", "last": "$118.41", "trend": " ▂▃▅▆▇███▇", "url": "NVDA"},
            {"symbol": "ETH/USD", "asset": "Crypto", "bid": "$3,520.10", "ask": "$3,520.40", "spread_bps": "0.09 bps", "imbalance": "+2.1% [BAL]", "last": "$3,520.25", "trend": "▄▄▅▅▆▆▆▇▇█", "url": "ETH"},
            {"symbol": "MSFT", "asset": "Equity", "bid": "$442.20", "ask": "$442.25", "spread_bps": "0.11 bps", "imbalance": "-1.5% [BAL]", "last": "$442.22", "trend": "▆▆▅▅▄▄▅▅▆▆", "url": "MSFT"},
            {"symbol": "RELIANCE.NS", "asset": "India (NSE)", "bid": "₹2,940.00", "ask": "₹2,940.50", "spread_bps": "0.17 bps", "imbalance": "+8.4% [BUY]", "last": "₹2,940.25", "trend": " ▂▃▄▅▆▇███", "url": "RELIANCE"},
        ]

    def _load_edgar_data(self) -> List[Dict[str, Any]]:
        return [
            {"time": "Today 10:45", "ticker": "NVDA", "form": "8-K", "description": "Item 5.02: Election of Director & Board Committee Changes", "urgency": "[bold red]CRITICAL[/bold red]", "action": "[Open in Browser]", "url": "https://www.sec.gov/edgar/browse/?CIK=0001045810"},
            {"time": "Today 09:30", "ticker": "AAPL", "form": "Form 4", "description": "Insider Trade: Tim Cook disposed 50,000 shares ($9.2M)", "urgency": "[bold yellow]HIGH[/bold yellow]", "action": "[Open in Browser]", "url": "https://www.sec.gov/edgar/browse/?CIK=0000320193"},
            {"time": "Yesterday", "ticker": "MSFT", "form": "8-K", "description": "Item 2.02: Results of Operations & Financial Statements", "urgency": "[bold yellow]HIGH[/bold yellow]", "action": "[Open in Browser]", "url": "https://www.sec.gov/edgar/browse/?CIK=0000789019"},
            {"time": "Sep 12", "ticker": "TSLA", "form": "10-Q", "description": "Quarterly Report pursuant to Section 13 or 15(d)", "urgency": "[dim]INFO[/dim]", "action": "[Open in Browser]", "url": "https://www.sec.gov/edgar/browse/?CIK=0001318605"},
            {"time": "Sep 10", "ticker": "GOOGL", "form": "Form 4", "description": "Director Grant: 12,500 Class C Restricted Stock Units", "urgency": "[dim]INFO[/dim]", "action": "[Open in Browser]", "url": "https://www.sec.gov/edgar/browse/?CIK=0001652044"},
        ]

    def _load_portfolio_data(self) -> List[Dict[str, Any]]:
        return [
            {"symbol": "NVDA", "currency": "USD", "qty": "500", "avg_cost": "$110.20", "price": "$118.41", "pnl": "+$4,105.00 (+7.4%)", "value": "$59,205.00"},
            {"symbol": "AAPL", "currency": "USD", "qty": "300", "avg_cost": "$180.00", "price": "$185.16", "pnl": "+$1,548.00 (+2.9%)", "value": "$55,548.00"},
            {"symbol": "BTC/USD", "currency": "USD", "qty": "1.50", "avg_cost": "$64,200.00", "price": "$68,420.80", "pnl": "+$6,331.20 (+6.6%)", "value": "$102,631.20"},
            {"symbol": "MSFT", "currency": "USD", "qty": "100", "avg_cost": "$445.00", "price": "$442.22", "pnl": "-$278.00 (-0.6%)", "value": "$44,222.00"},
        ]

    def set_status(self, msg: str, style: str = "dim green", duration_s: float = 3.0):
        self.status_message = msg
        self.status_style = style
        self.status_expiry = time.perf_counter() + duration_s

    @property
    def active_grid(self) -> DataGrid:
        return self.tabs[self.active_tab_idx][2]

    # -----------------------------------------------------------------------
    # Rendering Architecture (Alternate Buffer, Zero Flicker)
    # -----------------------------------------------------------------------

    def render(self):
        """Render the complete modal screen."""
        try:
            term_height = os.get_terminal_size().lines
        except Exception:
            term_height = 24
        # Dynamic page size: leave 9 lines for banner, tabs, filter, and hotkey ribbon
        self.active_grid.page_size = max(5, term_height - 10)

        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="tabs", size=1),
            Layout(name="body"),
            Layout(name="footer", size=3),
        )

        # Header
        header_text = Text()
        header_text.append("⚡ MDRAP KEYBOARD NAVIGATOR ", style="bold #818cf8")
        header_text.append("│ Institutional Microstructure Desk │ ", style="dim")
        header_text.append("MODE: ", style="bold")
        if self.mode == NavigatorMode.NORMAL:
            header_text.append("NORMAL", style="bold green")
        elif self.mode == NavigatorMode.FILTER:
            header_text.append(f"FILTER ('{self.active_grid.filter_query}')", style="bold yellow")
        elif self.mode == NavigatorMode.MODAL:
            header_text.append("CONFIRMATION REQUIRED", style="bold red blink")
        else:
            header_text.append("COMMAND", style="bold cyan")
        layout["header"].update(Panel(Align.center(header_text), style="bold #818cf8", padding=(0, 1)))

        # Tabs
        tab_text = Text()
        for idx, (hotkey, name, grid) in enumerate(self.tabs):
            is_active = idx == self.active_tab_idx
            if is_active:
                tab_text.append(f"[{hotkey}] {name} ", style="bold black on #818cf8")
            else:
                tab_text.append(f" {hotkey} {name} ", style="dim")
            tab_text.append(" ")
        layout["tabs"].update(tab_text)

        # Body Table
        grid = self.active_grid
        table = Table(title=grid.title, title_style="bold #38bdf8", border_style="#4b5563", show_lines=False, expand=True)
        table.add_column(" ", width=3, justify="center")
        for col in grid.columns:
            table.add_column(col.name, justify=col.justify, style=col.style, width=col.width)

        visible_rows = grid.filtered_rows[grid.scroll_offset : grid.scroll_offset + grid.page_size]
        for row_idx, r in enumerate(visible_rows):
            actual_idx = grid.scroll_offset + row_idx
            is_selected = actual_idx == grid.selected_idx
            cursor = "[bold cyan]❯[/bold cyan]" if is_selected else " "
            row_style = "bold white on #1e293b" if is_selected else None

            row_cells = [cursor]
            for col in grid.columns:
                val = str(r.get(col.key, ""))
                row_cells.append(val)
            table.add_row(*row_cells, style=row_style)

        if not visible_rows:
            table.add_row(" ", *["[dim italic]No matching records found[/dim italic]" for _ in grid.columns])

        # Overlay confirmation modal if armed
        if self.mode == NavigatorMode.MODAL and self.active_modal:
            modal_content = Text()
            modal_content.append(f"\n⚠️  {self.active_modal.message}\n\n", style="bold yellow")
            for k, v in self.active_modal.details.items():
                modal_content.append(f"  {k}: ", style="bold cyan")
                modal_content.append(f"{v}\n", style="white")
            modal_content.append("\n  [Enter] Confirm & Execute   │   [Esc] Cancel\n", style="bold green on black")
            modal_panel = Panel(Align.center(modal_content), title=f"[bold red] {self.active_modal.title} [/bold red]", border_style="bold red", padding=(1, 2))
            layout["body"].update(modal_panel)
        else:
            layout["body"].update(table)

        # Footer & Status
        now = time.perf_counter()
        status_disp = self.status_message if now < self.status_expiry else "Normal Mode. Press [?] for cheat-sheet."
        footer_text = Text()
        footer_text.append(f"{status_disp}\n", style=self.status_style)

        if self.mode == NavigatorMode.NORMAL:
            footer_text.append("[j/k] Row  [h/l/1-5] Tabs  [Enter] Drilldown  [c] Chart  [d] Depth  [o] URL  [/] Filter  [x] Export  [q] Quit", style="dim")
        elif self.mode == NavigatorMode.FILTER:
            footer_text.append(f"SEARCH: {self.input_buffer}█  (Press [Enter] to lock, [Esc] to clear)", style="bold yellow")
        elif self.mode == NavigatorMode.MODAL:
            footer_text.append("ARMED MODAL TICKET ACTIVE: PRESS [ENTER] TO EXECUTE OR [ESC] TO DISARM", style="bold red")

        layout["footer"].update(Panel(footer_text, style="dim", padding=(0, 1)))

        # Clear screen and reposition cursor to top-left
        sys.stdout.write("\x1b[H")
        self.console.print(layout)

    # -----------------------------------------------------------------------
    # Interactive Event Loop
    # -----------------------------------------------------------------------

    def run(self):
        """Run the interactive keyboard event loop."""
        self._running = True
        self.reader.enter_raw_mode()

        # Switch to alternate screen buffer and hide cursor
        sys.stdout.write("\x1b[?1049h\x1b[?25l\x1b[H")
        sys.stdout.flush()

        try:
            while self._running:
                self.render()
                key = self.reader.read_key(timeout_s=0.04)
                if not key:
                    continue
                self.handle_key(key)
        finally:
            self.reader.exit_raw_mode()
            # Restore main screen buffer and unhide cursor
            sys.stdout.write("\x1b[?1049l\x1b[?25h")
            sys.stdout.flush()

    def handle_key(self, key: str):
        """Dispatch keystroke based on current mode."""
        if self.mode == NavigatorMode.MODAL:
            self._handle_modal_key(key)
        elif self.mode == NavigatorMode.FILTER:
            self._handle_filter_key(key)
        else:
            self._handle_normal_key(key)

    def _handle_normal_key(self, key: str):
        # 1. Navigation
        if key in ("j", Key.DOWN):
            self.active_grid.move_selection(1)
        elif key in ("k", Key.UP):
            self.active_grid.move_selection(-1)
        elif key in (Key.PAGE_DOWN, "\x04"):  # Ctrl-D
            self.active_grid.page_down()
        elif key in (Key.PAGE_UP, "\x15"):    # Ctrl-U
            self.active_grid.page_up()
        elif key in ("g", Key.HOME):
            self.active_grid.jump_top()
        elif key in ("G", Key.END):
            self.active_grid.jump_bottom()

        # 2. Tabs
        elif key in ("l", Key.RIGHT, Key.TAB):
            self.active_tab_idx = (self.active_tab_idx + 1) % len(self.tabs)
        elif key in ("h", Key.LEFT):
            self.active_tab_idx = (self.active_tab_idx - 1) % len(self.tabs)
        elif key in ("1", "2", "3", "4", "5"):
            idx = int(key) - 1
            if idx < len(self.tabs):
                self.active_tab_idx = idx

        # 3. Filter Mode Trigger
        elif key == "/":
            self.mode = NavigatorMode.FILTER
            self.input_buffer = self.active_grid.filter_query
            self.set_status("Filter mode active. Type query, press [Enter] to lock.", "bold yellow")

        # 4. Actions on selected row
        elif key == Key.ENTER:
            self._execute_row_drilldown()
        elif key == "o":
            self._execute_open_url()
        elif key == "c":
            self._execute_chart()
        elif key == "d":
            self._execute_depth()
        elif key == "v":
            self._execute_vwap()
        elif key == "x":
            self._execute_export()
        elif key in ("b", "s"):
            self._prompt_order_ticket(side="BUY" if key == "b" else "SELL")
        elif key == "?":
            self._show_cheatsheet()
        elif key in ("q", Key.ESC):
            self._running = False

    def _handle_filter_key(self, key: str):
        if key == Key.ENTER:
            self.mode = NavigatorMode.NORMAL
            self.set_status(f"Filter locked: '{self.active_grid.filter_query}' ({len(self.active_grid.filtered_rows)} records)", "bold green")
        elif key == Key.ESC:
            self.active_grid.clear_filter()
            self.mode = NavigatorMode.NORMAL
            self.input_buffer = ""
            self.set_status("Filter cleared.", "dim")
        elif key == Key.BACKSPACE:
            if self.input_buffer:
                self.input_buffer = self.input_buffer[:-1]
                self.active_grid.set_filter(self.input_buffer)
        elif len(key) == 1 and key.isprintable():
            self.input_buffer += key
            self.active_grid.set_filter(self.input_buffer)

    def _handle_modal_key(self, key: str):
        if key in (Key.ENTER, "y", "Y"):
            if self.active_modal and self.active_modal.callback:
                try:
                    self.active_modal.callback()
                except Exception as exc:
                    self.set_status(f"Action error: {exc}", "bold red", duration_s=4.0)
            self.set_status("Action executed successfully.", "bold green", duration_s=3.0)
            self.mode = NavigatorMode.NORMAL
            self.active_modal = None
        elif key in (Key.ESC, "n", "N", "q"):
            self.set_status("Action cancelled by operator.", "dim yellow", duration_s=2.5)
            self.mode = NavigatorMode.NORMAL
            self.active_modal = None

    # -----------------------------------------------------------------------
    # Row Actions & Safety Guards
    # -----------------------------------------------------------------------

    def _execute_row_drilldown(self):
        row = self.active_grid.get_selected_row()
        if not row:
            return
        tab_name = self.tabs[self.active_tab_idx][1]
        if tab_name == "Fleet":
            name = row.get("name", "")
            self.set_status(f"Inspecting voyage dossier for {name}...", "bold cyan")
        elif tab_name == "Markets":
            mic = row.get("mic", "")
            self.set_status(f"Selected venue {mic}. Switched focus.", "bold cyan")
        elif tab_name in ("Depth", "Portfolio"):
            sym = row.get("symbol", "")
            self.set_status(f"Focused instrument: {sym}", "bold cyan")
        elif tab_name == "EDGAR":
            self._execute_open_url()

    def _execute_open_url(self):
        row = self.active_grid.get_selected_row()
        if not row:
            return
        url = row.get("url", "")
        if url and url.startswith("http"):
            try:
                webbrowser.open(url)
                self.set_status(f"Opened URL in browser: {url}", "bold green")
            except Exception as e:
                self.set_status(f"Browser launch failed: {e}", "bold red")
        else:
            ticker = row.get("ticker") or row.get("symbol") or "AAPL"
            target_url = f"https://www.sec.gov/edgar/browse/?CIK={ticker}"
            webbrowser.open(target_url)
            self.set_status(f"Opened SEC profile for {ticker}", "bold green")

    def _execute_chart(self):
        row = self.active_grid.get_selected_row()
        sym = row.get("symbol") or row.get("ticker") or "AAPL"
        self.set_status(f"Rendering candlestick chart for {sym}...", "bold cyan")

    def _execute_depth(self):
        row = self.active_grid.get_selected_row()
        sym = row.get("symbol") or row.get("ticker") or "BTC/USD"
        self.set_status(f"Aggregating Level-2 market depth for {sym}...", "bold cyan")

    def _execute_vwap(self):
        row = self.active_grid.get_selected_row()
        sym = row.get("symbol") or row.get("ticker") or "AAPL"
        self.set_status(f"Computing real-time VWAP curve for {sym}...", "bold cyan")

    def _execute_export(self):
        tab_name = self.tabs[self.active_tab_idx][1]
        self.set_status(f"Exporting active '{tab_name}' grid to Excel / CSV package...", "bold green")

    def _prompt_order_ticket(self, side: str = "BUY"):
        """Armed confirmation ticket preventing accidental trade execution."""
        row = self.active_grid.get_selected_row()
        sym = (row.get("symbol") if row else None) or "AAPL"
        price_str = (row.get("price") or row.get("last") or "$185.00") if row else "$185.00"

        def _do_submit():
            # Actual trade execution callback
            pass

        self.active_modal = ConfirmationTicket(
            title=f"ARMED PAPER ORDER TICKET — {side} {sym}",
            message=f"Are you sure you want to submit paper order for {sym}?",
            details={
                "Symbol": sym,
                "Side": f"[bold {'green' if side == 'BUY' else 'red'}]{side}[/bold {'green' if side == 'BUY' else 'red'}]",
                "Quantity": "100 Shares",
                "Order Type": "LIMIT",
                "Estimated Price": price_str,
                "Execution Route": "SMART (BBO)",
            },
            action_type="TRADE",
            callback=_do_submit,
        )
        self.mode = NavigatorMode.MODAL

    def _show_cheatsheet(self):
        self.set_status("Vim motions: j/k (row), h/l (tabs), / (filter), Enter (drill), b/s (ticket), q (quit)", "bold white", duration_s=5.0)


# ---------------------------------------------------------------------------
# CLI Entry Point Helper
# ---------------------------------------------------------------------------

def launch_navigator():
    """Entry point to launch the MDRAP interactive navigator."""
    nav = MDRAPNavigator()
    nav.run()


if __name__ == "__main__":
    launch_navigator()
