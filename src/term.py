# terminal rendering abstraction with stdlib fallback
import re
import sys
from typing import Any, List, Optional

from rich.console import Console, Group
from rich.table import Table
from rich.panel import Panel
from io import StringIO

_TAG_RE = re.compile(r'\[/?[a-zA-Z0-9_# =,-]+\]')

def strip_tags(text: Any) -> str:
    return _TAG_RE.sub('', str(text))

class _StdlibTable(Table):
    """Compatibility wrapper rendering rich Table to plain text."""
    def __str__(self) -> str:
        s = StringIO()
        Console(file=s, force_terminal=False, color_system=None).print(self)
        return s.getvalue()

class _StdlibPanel(Panel):
    """Compatibility wrapper rendering rich Panel to plain text."""
    def __str__(self) -> str:
        s = StringIO()
        Console(file=s, force_terminal=False, color_system=None).print(self)
        return s.getvalue()

_StdlibConsole = Console


def render_gemini_banner(console: Any) -> None:
    """Render retro gradient block banner matching Google Gemini CLI aesthetic."""
    banner_text = (
        "[bold cyan]>[/bold cyan]  [bold #38bdf8]███╗   ███╗[/bold #38bdf8][bold #60a5fa]██████╗ [/bold #60a5fa][bold #818cf8]██████╗ [/bold #818cf8][bold #a78bfa] █████╗ [/bold #a78bfa][bold #c084fc]██████╗ [/bold #c084fc]\n"
        "[bold cyan]>[/bold cyan]  [bold #38bdf8]████╗ ████║[/bold #38bdf8][bold #60a5fa]██╔══██╗[/bold #60a5fa][bold #818cf8]██╔══██╗[/bold #818cf8][bold #a78bfa]██╔══██╗[/bold #a78bfa][bold #c084fc]██╔══██╗[/bold #c084fc]\n"
        "[bold cyan]>[/bold cyan]  [bold #38bdf8]██╔████╔██║[/bold #38bdf8][bold #60a5fa]██║  ██║[/bold #60a5fa][bold #818cf8]██████╔╝[/bold #818cf8][bold #a78bfa]███████║[/bold #a78bfa][bold #c084fc]██████╔╝[/bold #c084fc]\n"
        "[bold cyan]>[/bold cyan]  [bold #38bdf8]██║╚██╔╝██║[/bold #38bdf8][bold #60a5fa]██║  ██║[/bold #60a5fa][bold #818cf8]██╔══██╗[/bold #818cf8][bold #a78bfa]██╔══██║[/bold #a78bfa][bold #c084fc]██╔═══╝ [/bold #c084fc]\n"
        "[bold cyan]>[/bold cyan]  [bold #38bdf8]██║ ╚═╝ ██║[/bold #38bdf8][bold #60a5fa]██████╔╝[/bold #60a5fa][bold #818cf8]██║  ██║[/bold #818cf8][bold #a78bfa]██║  ██║[/bold #a78bfa][bold #c084fc]██║     [/bold #c084fc]\n"
        "[bold cyan]>[/bold cyan]  [bold #38bdf8]╚═╝     ╚═╝[/bold #38bdf8][bold #60a5fa]╚═════╝ [/bold #60a5fa][bold #818cf8]╚═╝  ╚═╝[/bold #818cf8][bold #a78bfa]╚═╝  ╚═╝[/bold #a78bfa][bold #c084fc]╚═╝     [/bold #c084fc]"
    )
    panel = Panel(banner_text, border_style="#818cf8", expand=False)
    console.print(panel)


def render_gemini_tips(console: Any) -> None:
    """Render Wall Street & modern terminal getting started tips."""
    tips = (
        "[dim]Tips for getting started (Wall Street Quick Commands):[/dim]\n"
        "  [bold #38bdf8]• Instant Mnemonics:[/bold #38bdf8] [bold green]BBO[/bold green][dim],[/dim] [bold green]LIVE[/bold green][dim],[/dim] [bold green]TOP[/bold green][dim],[/dim] [bold green]CND[/bold green][dim],[/dim] [bold green]VOL[/bold green][dim],[/dim] [bold green]STAT[/bold green][dim],[/dim] [bold green]SUB[/bold green][dim],[/dim] [bold green]TEST[/bold green]\n"
        "  [bold #38bdf8]• Ticker-First Syntax:[/bold #38bdf8] [bold cyan]BTC BBO[/bold cyan][dim],[/dim] [bold cyan]AAPL CND[/bold cyan][dim],[/dim] [bold cyan]ETH LIVE[/bold cyan] [dim](or /bbo BTC, /live)[/dim]\n"
        "  [bold #38bdf8]• Fast 1-Key Launch:[/bold #38bdf8] [dim]Type[/dim] [bold yellow]1[/bold yellow] [dim]for Live Stream,[/dim] [bold yellow]2[/bold yellow] [dim]for BBO,[/dim] [bold yellow]3[/bold yellow] [dim]for Cockpit,[/dim] [bold yellow]5[/bold yellow] [dim]for Status[/dim]\n"
        "  [bold #38bdf8]• Command Palette:[/bold #38bdf8] [dim]Type[/dim] [bold cyan]?[/bold cyan] [dim]or[/dim] [bold cyan]/help[/bold cyan] [dim]for categorized command matrix[/dim]\n"
    )
    console.print(tips)


def _get_term_width(console: Any, default: int = 70) -> int:
    try:
        if hasattr(console, "width") and console.width:
            return max(40, min(80, console.width - 2))
        import shutil
        cols = shutil.get_terminal_size((70, 20)).columns
        return max(40, min(80, cols - 2))
    except Exception:
        return default


def render_gemini_box_top(console: Any, db_path: str = "data/mdrap.db", width: Optional[int] = None) -> None:
    """Render Gemini-CLI status header and top border of the input container."""
    if width is None:
        width = _get_term_width(console)
    left_header = "Using 1 GEMINI.md file"
    right_header = "1 Native C DLL (24ns)"
    if width < len(left_header) + len(right_header) + 4:
        console.print(f"[dim #818cf8]{left_header} | {right_header}[/dim #818cf8]")
    else:
        space = max(2, width - len(left_header) - len(right_header))
        console.print(f"[dim #818cf8]{left_header}{' ' * space}{right_header}[/dim #818cf8]")
    console.print(f"[bold #818cf8]┌{'─' * max(10, width - 2)}┐[/bold #818cf8]")


def render_gemini_box_bottom(console: Any, db_path: str = "data/mdrap.db", width: Optional[int] = None) -> None:
    """Render bottom border and footer status bar with auto-responsive width."""
    if width is None:
        width = _get_term_width(console)
    console.print(f"[bold #818cf8]└{'─' * max(10, width - 2)}┘[/bold #818cf8]")
    left_footer = f"~/{db_path}"
    right_footer = "mdrap-v3-fastpath"
    
    if width < 62:
        console.print(f"[dim #64748b]{left_footer}  {right_footer}[/dim #64748b]\n")
    else:
        center_footer = "Streaming V2 (backpressure)"
        total_text = len(left_footer) + len(center_footer) + len(right_footer)
        rem = max(2, width - total_text)
        s1 = rem // 2
        s2 = rem - s1
        console.print(f"[dim #64748b]{left_footer}{' ' * s1}{center_footer}{' ' * s2}{right_footer}[/dim #64748b]\n")


