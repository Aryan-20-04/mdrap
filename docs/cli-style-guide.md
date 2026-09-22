# MDRAP CLI Visual Design & Usability Style Guide

This specification defines the visual standards, accessibility requirements, and interaction conventions across all MDRAP terminal user interfaces and command-line entry points.

---

## 1. Core Principles

1. **Information Density with Institutional Clarity**: Every line and screen character must provide immediate market or operational value. Avoid decorative bloat.
2. **Colorblind-Safe by Default**: Never rely on color alone to convey state or direction. Every metric status must combine distinct geometry (Unicode glyphs) with color.
3. **Zero ANSI Bleed / Clean Scriptability**: Any command invoked with `--no-color`, `--plain`, or `NO_COLOR=1` must output pure plain text with zero escape sequences, suitable for CI logs and file piping.
4. **Resilient Muscle Memory**: Trimming subparser choices must never break existing scripts or trader shortcuts. Secondary mnemonics route transparently via the mnemonic mapper.

---

## 2. Border & Box Styles

All terminal tables and callout panels across MDRAP must standardize on **rounded boxes**:

- **Border Type**: `rich.box.ROUNDED`
- **Implementation**: Import `Table` and `Panel` from `src/term.py`, which defaults all tables to `box=box.ROUNDED`.
- **Panels**: Use `Panel(..., border_style="cyan")` for headers and status summaries. Use `yellow` only for degraded states, and `red` for emergency alerts.

```python
from term import Table, Panel

table = Table(title="Feed Reliability", expand=True)
# Automatically uses box.ROUNDED
```

---

## 3. Colorblind-Safe Semantic Status Indicators

Status and direction must never rely on green/red contrast alone. Always use `format_status()` and `format_direction()` from `src/term.py`.

### 3.1 Platform & Pipeline Status

| State | Glyph | Label | Rich Markup | Rendering |
| :--- | :---: | :--- | :--- | :--- |
| **Healthy / Valid** | `●` | `VALID` / `PASS` / `HEALTHY` / `OK` | `[bold green]● VALID[/bold green]` | `● VALID` |
| **Degraded / Warning** | `▲` | `SUSPICIOUS` / `WARN` / `DEGRADED` | `[bold yellow]▲ SUSPICIOUS[/bold yellow]` | `▲ SUSPICIOUS` |
| **Failed / Invalid** | `✕` | `INVALID` / `FAIL` / `ERROR` | `[bold red]✕ INVALID[/bold red]` | `✕ INVALID` |
| **Neutral / Unknown** | | Custom text | `[dim]{status}[/dim]` | `{status}` |

### 3.2 Market Price Movement & Direction

Direction indicators combine an explicit directional arrow with formatting:

| Movement | Glyph | Formatting | Example |
| :--- | :---: | :--- | :--- |
| **Up / Gain / Buy** | `▲` | `[bold green]▲ ${val:,.2f}[/bold green]` | `▲ $78,120.50` |
| **Down / Loss / Sell** | `▼` | `[bold red]▼ ${val:,.2f}[/bold red]` | `▼ $77,950.00` |
| **Unchanged / Neutral** | `■` | `[dim]■ ${val:,.2f}[/dim]` | `■ $78,000.00` |

---

## 4. Number & Metric Formatting

All numbers must be formatted with commas for thousands and explicit decimal bounds:

- **Integers (events, packets, counts)**: `{val:,}` (e.g., `1,000,000`, `50,000`).
- **Currency Prices**: `${val:,.2f}` (e.g., `$182.50`, `$78,210.00`).
- **Percentages**: `{val:.1f}%` or `{val:.2f}%` (e.g., `99.4%`, `0.60%`).
- **Latencies**: Microseconds `us` with 2 decimals for averages, integers for percentiles (e.g., `52.40 us`, `p50: 15 us / p99: 48 us`).
- **Throughput**: `{eps:,.0f} eps` (e.g., `1,024,500 eps`).

Use the `format_num(val, decimals=2)` helper in `src/term.py` for consistent rendering.

---

## 5. Color Suppression Contract (`NO_COLOR`)

MDRAP strictly follows the open [`NO_COLOR`](https://no-color.org) specification:

1. **Environment Variables**: When `NO_COLOR=1` or `MDRAP_NO_COLOR=1` is set in the environment, all ANSI styling is automatically suppressed across all `Console` instances.
2. **Global CLI Flags**: `--no-color` and `--plain` are supported as global flags on the root `mdrap` parser and all individual subparsers. Passing either flag immediately sets `NO_COLOR=1` in `os.environ` before any console is initialized.
3. **Structured JSON Output**: `--json` produces clean, machine-readable JSON without any formatting or ANSI codes.

---

## 6. Command Surface Ergonomics & Alias Discipline

To keep CLI error messages, `--help` screens, and tab completion fast and readable:

1. **Argparse Choices**: Subparsers registered via `_sub(...)` maintain at most **one** short primary shortcut (e.g., `status: ["s"]`, `run: ["r"]`, `bbo: ["nbbo"]`, `depth: ["l2"]`, `analytics: ["a"]`).
2. **Mnemonic Map Layer**: Secondary aliases (`navigator`, `ladder`, `book`, `whales`, `compact`, `prune`, `cvd`, etc.) are routed through `MNEMONIC_MAP` in `main()` and the interactive shell (`cmd_shell`). Muscle memory and existing scripts are 100% preserved.
3. **Error Palette Fallback**: When an unrecognized command or choice is entered, instead of dumping 150+ raw choices into terminal stderr, MDRAP displays a targeted error message followed by the clean 4-quadrant command palette (`render_command_palette`).
4. **Shell Completion**: Fast, static auto-completion scripts are generated on demand via:
   ```bash
   mdrap completion bash >> ~/.bashrc
   mdrap completion zsh >> ~/.zshrc
   mdrap completion fish > ~/.config/fish/completions/mdrap.fish
   mdrap completion powershell >> $PROFILE
   ```
