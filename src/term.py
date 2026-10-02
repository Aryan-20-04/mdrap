# terminal rendering abstraction with stdlib fallback
import os
import re
import sys
from typing import Any, Optional, Sequence, Dict
from io import StringIO


from rich import box
from rich.console import Console as _RichConsole
from rich.table import Table as _RichTable
from rich.panel import Panel

__stability__ = "stable"

_TAG_RE = re.compile(r"\[/?[a-zA-Z0-9_# =,-]+\]")


def is_no_color_active() -> bool:
    """Check if color suppression is explicitly requested via flag or NO_COLOR environment."""
    return bool(os.environ.get("NO_COLOR") or os.environ.get("MDRAP_NO_COLOR"))


class Console(_RichConsole):
    """Rich Console that automatically suppresses ANSI styling when NO_COLOR is active."""

    def __init__(self, *args, **kwargs):
        if is_no_color_active():
            kwargs.setdefault("color_system", None)
            kwargs.setdefault("no_color", True)
        super().__init__(*args, **kwargs)


class Table(_RichTable):
    """Rich Table with standard institutional rounded box border across MDRAP."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("box", box.ROUNDED)
        super().__init__(*args, **kwargs)


def strip_tags(text: Any) -> str:
    return _TAG_RE.sub("", str(text))


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


def format_status(status: str) -> str:
    """Colorblind-safe status indicator combining distinct Unicode glyph with styling."""
    no_color = is_no_color_active()
    s = str(status).upper()
    if s in ("VALID", "PASS", "HEALTHY", "OK", "READY"):
        lbl = s if s in ("PASS", "HEALTHY", "OK", "READY") else "VALID"
        if no_color:
            return f"● {lbl}"
        return f"[bold green]● {lbl}[/bold green]"
    elif s in ("SUSPICIOUS", "WARN", "WARNING", "DEGRADED", "FALLBACK", "UNCOMPILED"):
        lbl = (
            s
            if s in ("WARN", "WARNING", "DEGRADED", "FALLBACK", "UNCOMPILED")
            else "SUSPICIOUS"
        )
        if no_color:
            return f"▲ {lbl}"
        return f"[bold yellow]▲ {lbl}[/bold yellow]"
    elif s in ("INVALID", "FAIL", "FAILED", "ERROR", "CROSS", "CROSSED"):
        lbl = s if s in ("FAIL", "FAILED", "ERROR", "CROSS", "CROSSED") else "INVALID"
        if no_color:
            return f"✕ {lbl}"
        return f"[bold red]✕ {lbl}[/bold red]"
    if no_color:
        return f"{status}"
    return f"[dim]{status}[/dim]"


def format_direction(direction: str, val: float, curr: str = "$") -> str:
    """Colorblind-safe market movement indicator with directional arrows and styling."""
    no_color = is_no_color_active()
    d = str(direction).upper()
    if d in ("UP", "BUY", "GAIN"):
        if no_color:
            return f"▲ {curr}{val:,.2f}"
        return f"[bold green]▲ {curr}{val:,.2f}[/bold green]"
    elif d in ("DOWN", "SELL", "LOSS"):
        if no_color:
            return f"▼ {curr}{val:,.2f}"
        return f"[bold red]▼ {curr}{val:,.2f}[/bold red]"
    if no_color:
        return f"■ {curr}{val:,.2f}"
    return f"[dim]■ {curr}{val:,.2f}[/dim]"


def format_num(val: float | int, decimals: int = 2) -> str:
    """Standardized institutional number formatting with commas."""
    if isinstance(val, int):
        return f"{val:,}"
    return f"{val:,.{decimals}f}"


def format_latency(us: float) -> str:
    """Format latency with auto-scaling units (ns/µs/ms) and subtle context colors."""
    no_color = is_no_color_active()
    if us < 0.001:
        text = f"{us * 1_000_000:.0f} ps"
        style = "green"
    elif us < 1.0:
        text = f"{us * 1000:.1f} ns"
        style = "bold green"
    elif us < 50.0:
        text = f"{us:.2f} µs"
        style = "bold green"
    elif us < 250.0:
        text = f"{us:.2f} µs"
        style = "cyan"
    elif us < 1000.0:
        text = f"{us:.1f} µs"
        style = "yellow"
    else:
        text = f"{us / 1000.0:.2f} ms"
        style = "bold red"

    if no_color:
        return text
    return f"[{style}]{text}[/{style}]"


def format_rate(rate: float, unit: str = "eps") -> str:
    """Format throughput rate with thousands separators or compact SI suffix."""
    if rate >= 1_000_000:
        return f"{rate / 1_000_000:.2f}M {unit}"
    elif rate >= 10_000:
        return f"{rate:,.0f} {unit}"
    elif rate >= 1_000:
        return f"{rate:,.1f} {unit}"
    return f"{rate:.1f} {unit}"


def render_brand_header(
    console: Console,
    title: str = "MDRAP",
    subtitle: str = "Market Data Reliability & Acceleration Platform",
    badge: Optional[str] = None,
    meta: Optional[str] = None,
) -> None:
    """Modern, compact developer CLI header with crisp typography and subtle badges."""
    badge_str = f" [bold green]● {badge}[/bold green]" if badge else ""
    meta_str = f" [dim]• {meta}[/dim]" if meta else ""
    header_line = f"[bold cyan]◆ {title}[/bold cyan]{badge_str}{meta_str}"
    sub_line = f"  [dim]{subtitle}[/dim]"
    console.print(header_line)
    console.print(sub_line)
    console.print()


def render_step_start(
    console: Console, title: str, details: Optional[Dict[str, Any]] = None
) -> None:
    """Render the opening of an operation with a step indicator and optional parameter tree."""
    console.print(f"[bold cyan]◆[/bold cyan] [bold white]{title}[/bold white]")
    if details:
        items = list(details.items())
        for idx, (k, v) in enumerate(items):
            branch = "└─" if idx == len(items) - 1 else "├─"
            console.print(f"  [dim]{branch}[/dim] [dim]{k}:[/dim] [white]{v}[/white]")


def render_step_success(
    console: Console, title: str, stats: Optional[str] = None
) -> None:
    """Render a successfully completed step with green checkmark."""
    stats_str = f" [dim]({stats})[/dim]" if stats else ""
    console.print(
        f"[bold green]✓[/bold green] [bold white]{title}[/bold white]{stats_str}"
    )


def render_step_warn(console: Console, title: str, note: Optional[str] = None) -> None:
    """Render a warning step indicator."""
    note_str = f" [dim]({note})[/dim]" if note else ""
    console.print(
        f"[bold yellow]▲[/bold yellow] [bold white]{title}[/bold white]{note_str}"
    )


def render_step_fail(console: Console, title: str, error: Optional[str] = None) -> None:
    """Render a failed step indicator."""
    err_str = f" [red]{error}[/red]" if error else ""
    console.print(f"[bold red]✕[/bold red] [bold white]{title}[/bold white]{err_str}")


def render_summary_card(
    console: Console,
    title: str,
    sections: Sequence[Sequence[tuple[str, str]]],
    meta: Optional[str] = None,
    border_style: str = "cyan",
) -> None:
    """Render a modern, high-density summary card with section dividers."""
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", justify="left", no_wrap=True)
    grid.add_column(style="bold white", justify="left")
    grid.add_column(style="dim", justify="left", no_wrap=True)
    grid.add_column(style="bold white", justify="left")

    for sec_idx, section in enumerate(sections):
        if sec_idx > 0:
            grid.add_row("", "", "", "")
        for i in range(0, len(section), 2):
            k1, v1 = section[i]
            if i + 1 < len(section):
                k2, v2 = section[i + 1]
                grid.add_row(f"{k1}:", v1, f"{k2}:", v2)
            else:
                grid.add_row(f"{k1}:", v1, "", "")

    title_text = f"[bold cyan]{title}[/bold cyan]"
    if meta:
        title_text += f" [dim]({meta})[/dim]"
    panel = Panel(
        grid,
        title=title_text,
        title_align="left",
        border_style=border_style,
        box=box.ROUNDED,
    )
    console.print(panel)


def render_error_card(
    console: Console,
    title: str,
    message: str,
    suggestion: Optional[str] = None,
    command: Optional[str] = None,
) -> None:
    """Render an actionable error card with root cause and suggested remedy."""
    lines = [f"[bold red]✕ {message}[/bold red]"]
    if suggestion:
        lines.append(
            f"\n[bold yellow]💡 Suggestion:[/bold yellow] [white]{suggestion}[/white]"
        )
    if command:
        lines.append(f"[bold cyan]👉 Try:[/bold cyan] [bold cyan]{command}[/bold cyan]")

    content = "\n".join(lines)
    panel = Panel(
        content,
        title=f"[bold red]Error: {title}[/bold red]",
        title_align="left",
        border_style="red",
        box=box.ROUNDED,
    )
    console.print(panel)


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


def render_gemini_box_top(
    console: Any, db_path: str = "data/mdrap.db", width: Optional[int] = None
) -> None:
    """Render Gemini-CLI status header and top border of the input container."""
    if width is None:
        width = _get_term_width(console)
    left_header = "Using 1 GEMINI.md file"
    right_header = "1 Native C DLL (24ns)"
    if width < len(left_header) + len(right_header) + 4:
        console.print(f"[dim #818cf8]{left_header} | {right_header}[/dim #818cf8]")
    else:
        space = max(2, width - len(left_header) - len(right_header))
        console.print(
            f"[dim #818cf8]{left_header}{' ' * space}{right_header}[/dim #818cf8]"
        )
    console.print(f"[bold #818cf8]┌{'─' * max(10, width - 2)}┐[/bold #818cf8]")


def render_gemini_box_bottom(
    console: Any, db_path: str = "data/mdrap.db", width: Optional[int] = None
) -> None:
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
        console.print(
            f"[dim #64748b]{left_footer}{' ' * s1}{center_footer}{' ' * s2}{right_footer}[/dim #64748b]\n"
        )
