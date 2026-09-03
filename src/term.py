# terminal rendering abstraction with stdlib fallback
import re
import sys
from typing import Any, List, Optional

try:
    from rich.console import Console as _RichConsole, Group as _RichGroup
    from rich.table import Table as _RichTable
    from rich.panel import Panel as _RichPanel
    _HAS_RICH = True
except ImportError:
    _HAS_RICH = False

_TAG_RE = re.compile(r'\[/?[a-zA-Z0-9_# =,-]+\]')

def strip_tags(text: Any) -> str:
    return _TAG_RE.sub('', str(text))

class _StdlibTable:
    def __init__(self, title: Optional[str] = None, show_lines: bool = False, expand: bool = False, **kwargs):
        self.title = title
        self.columns = []
        self.rows = []

    def add_column(self, header: str, justify: str = 'left', style: Any = None, no_wrap: bool = False, **kwargs):
        self.columns.append({'header': header, 'justify': justify})

    def add_row(self, *values: Any, **kwargs):
        self.rows.append([str(v) for v in values])

    def __str__(self) -> str:
        clean_cols = [strip_tags(c['header']) for c in self.columns]
        clean_rows = [[strip_tags(v) for v in r] for r in self.rows]
        
        col_widths = [len(c) for c in clean_cols]
        for row in clean_rows:
            for i, val in enumerate(row):
                if i < len(col_widths):
                    col_widths[i] = max(col_widths[i], len(val))
                else:
                    col_widths.append(len(val))
        
        while len(clean_cols) < len(col_widths):
            clean_cols.append(f'Col{len(clean_cols)}')
        
        total_w = sum(col_widths) + (3 * len(col_widths)) + 1
        lines = []
        if self.title:
            lines.append(f'-- {strip_tags(self.title)} '.ljust(total_w, '-'))
        
        # Header
        hdr_cells = [clean_cols[i].ljust(col_widths[i]) for i in range(len(col_widths))]
        lines.append('| ' + ' | '.join(hdr_cells) + ' |')
        lines.append('+' + '+'.join(['-' * (w + 2) for w in col_widths]) + '+')
        
        # Rows
        for row in clean_rows:
            row_cells = []
            for i in range(len(col_widths)):
                val = row[i] if i < len(row) else ''
                just = self.columns[i]['justify'] if i < len(self.columns) else 'left'
                if just == 'right':
                    row_cells.append(val.rjust(col_widths[i]))
                elif just == 'center':
                    row_cells.append(val.center(col_widths[i]))
                else:
                    row_cells.append(val.ljust(col_widths[i]))
            lines.append('| ' + ' | '.join(row_cells) + ' |')
        
        return '\n'.join(lines)

class _StdlibPanel:
    def __init__(self, renderable: Any, title: Optional[str] = None, border_style: Any = None, **kwargs):
        self.renderable = str(renderable)
        self.title = title

    @classmethod
    def fit(cls, renderable: Any, title: Optional[str] = None, **kwargs):
        return cls(renderable, title=title, **kwargs)

    def __str__(self) -> str:
        clean = strip_tags(self.renderable)
        lines = clean.splitlines()
        max_w = max(len(l) for l in lines) if lines else 40
        if self.title:
            max_w = max(max_w, len(strip_tags(self.title)) + 4)
        max_w = max(max_w, 40)
        
        box_lines = []
        top = f'+-- {strip_tags(self.title)} ' if self.title else '+-'
        box_lines.append(top.ljust(max_w + 3, '-') + '+')
        for l in lines:
            box_lines.append(f'| {l.ljust(max_w)} |')
        box_lines.append('+' + '-' * (max_w + 2) + '+')
        return '\n'.join(box_lines)

class _StdlibConsole:
    def print(self, *args: Any, **kwargs):
        out = []
        for a in args:
            if isinstance(a, (_StdlibTable, _StdlibPanel)):
                out.append(str(a))
            else:
                out.append(strip_tags(a))
        print(' '.join(out), file=kwargs.get('file', sys.stdout))

    def input(self, prompt: str = '') -> str:
        return input(strip_tags(prompt))

if _HAS_RICH:
    Console = _RichConsole
    Table = _RichTable
    Panel = _RichPanel
    Group = _RichGroup
else:
    Console = _StdlibConsole
    Table = _StdlibTable
    Panel = _StdlibPanel
    Group = list


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
    if _HAS_RICH:
        panel = Panel(banner_text, border_style="#818cf8", expand=False)
        console.print(panel)
    else:
        plain_banner = (
            ">  M D R A P  -- Market Data Reliability & Acceleration Platform\n"
            ">  ================================================================"
        )
        panel = _StdlibPanel(plain_banner)
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


