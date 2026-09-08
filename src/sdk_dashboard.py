"""
MDRAP External TUI Dashboard
Connects to the MDRAP TCP Gateway via the MDrapClient SDK and renders a 
multi-pane, high-performance terminal UI using Rich.
"""
import sys
import os
import time
import asyncio
from typing import Dict, List, Any

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.align import Align

from sdk.client import MDrapClient

# Tracking state
class DashboardState:
    def __init__(self):
        self.bbo: Dict[str, Dict[str, float]] = {}
        self.vwap: Dict[str, Dict[str, float]] = {} # symbol -> {vol, vol_price}
        self.events_received = 0
        self.last_events = 0
        self.throughput_history: List[int] = [0] * 40
        self.start_time = time.time()
        self.status = "DISCONNECTED"

state = DashboardState()

def create_layout() -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main"),
        Layout(name="footer", size=3)
    )
    layout["main"].split_row(
        Layout(name="bbo", ratio=1),
        Layout(name="vwap", ratio=1)
    )
    return layout

def generate_sparkline(data: List[int]) -> str:
    """Generates an ASCII sparkline from a list of ints."""
    if not data: return ""
    ticks = [' ', '▂', '▃', '▄', '▅', '▆', '▇', '█']
    min_val = min(data)
    max_val = max(data)
    if max_val == min_val:
        return " " * len(data)
    
    range_val = max_val - min_val
    spark = ""
    for v in data:
        idx = int(((v - min_val) / range_val) * (len(ticks) - 1))
        spark += ticks[idx]
    return spark

def render_bbo_table() -> Panel:
    table = Table(box=None, expand=True)
    table.add_column("Symbol", style="cyan", justify="left")
    table.add_column("Bid", style="green", justify="right")
    table.add_column("Spread", style="white", justify="right")
    table.add_column("Ask", style="red", justify="right")
    
    for sym, data in sorted(state.bbo.items())[:15]:
        bid = data.get("bid", 0.0)
        ask = data.get("ask", 0.0)
        spread = ask - bid if ask and bid else 0.0
        table.add_row(
            sym,
            f"{bid:.2f}" if bid else "-",
            f"{spread:.3f}" if spread else "-",
            f"{ask:.2f}" if ask else "-"
        )
    return Panel(table, title="[bold green]Live Top of Book (BBO)[/bold green]", border_style="green")

def render_vwap_table() -> Panel:
    table = Table(box=None, expand=True)
    table.add_column("Symbol", style="cyan", justify="left")
    table.add_column("VWAP", style="yellow", justify="right")
    table.add_column("Volume", style="magenta", justify="right")
    
    for sym, data in sorted(state.vwap.items())[:15]:
        vol = data.get("vol", 0.0)
        vol_price = data.get("vol_price", 0.0)
        v = (vol_price / vol) if vol > 0 else 0.0
        table.add_row(sym, f"${v:.3f}", f"{int(vol):,}")
        
    return Panel(table, title="[bold yellow]Execution VWAP Tracking[/bold yellow]", border_style="yellow")

def render_header() -> Panel:
    status_color = "green" if state.status == "CONNECTED" else "red"
    header_text = Text()
    header_text.append("MDRAP QUANTITATIVE DASHBOARD", style="bold white")
    header_text.append(f" | Gateway Status: ")
    header_text.append(f"{state.status}", style=f"bold {status_color}")
    header_text.append(f" | Events Processed: {state.events_received:,}", style="cyan")
    return Panel(Align.center(header_text), style="bold blue")

def render_footer() -> Panel:
    spark = generate_sparkline(state.throughput_history)
    eps = state.throughput_history[-1] if state.throughput_history else 0
    return Panel(f"Throughput: {eps:,} eps | [cyan]{spark}[/cyan]", title="Network Telemetry", border_style="blue")

def build_dashboard() -> Layout:
    layout = create_layout()
    layout["header"].update(render_header())
    layout["bbo"].update(render_bbo_table())
    layout["vwap"].update(render_vwap_table())
    layout["footer"].update(render_footer())
    return layout

async def run_dashboard(host: str = "127.0.0.1", port: int = 9000):
    client = MDrapClient(host=host, port=port)
    
    def on_event(msg: Dict[str, Any]):
        state.events_received += 1
        sym = msg.get("instrument")
        evt_type = msg.get("event_type")
        price = msg.get("price")
        size = msg.get("size")
        
        if not sym or price is None:
            return
            
        if evt_type == "QUOTE":
            if sym not in state.bbo:
                state.bbo[sym] = {"bid": 0.0, "ask": 0.0}
            if size > 0: # Mock bid logic
                state.bbo[sym]["bid"] = price
                state.bbo[sym]["ask"] = price + 0.02 # Synthetic spread for simulation
        elif evt_type == "TRADE":
            if sym not in state.vwap:
                state.vwap[sym] = {"vol": 0.0, "vol_price": 0.0}
            state.vwap[sym]["vol"] += size
            state.vwap[sym]["vol_price"] += (price * size)

    def on_system(msg: Dict[str, Any]):
        state.status = "CONNECTED"
        
    client.on("event", on_event)
    client.on("system", on_system)
    
    # Start the SDK in the background
    network_task = asyncio.create_task(client.subscribe())
    
    console = Console()
    with Live(build_dashboard(), console=console, refresh_per_second=10, screen=True) as live:
        try:
            while not network_task.done():
                # Telemetry math
                current = state.events_received
                diff = current - state.last_events
                state.last_events = current
                state.throughput_history.append(diff * 10) # 10Hz refresh means diff is per 0.1s
                if len(state.throughput_history) > 40:
                    state.throughput_history.pop(0)
                    
                live.update(build_dashboard())
                await asyncio.sleep(0.1)
                
            if network_task.done() and network_task.exception():
                # Display the connection error briefly before exiting
                state.status = f"ERROR: {network_task.exception()}"
                live.update(build_dashboard())
                await asyncio.sleep(3.0)
                
        except asyncio.CancelledError:
            pass
        finally:
            await client.close()

if __name__ == "__main__":
    try:
        asyncio.run(run_dashboard())
    except KeyboardInterrupt:
        sys.exit(0)
