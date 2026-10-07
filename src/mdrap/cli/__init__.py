"""MDRAP CLI parser, compatibility facade, and interactive shell."""

from __future__ import annotations

__stability__ = "beta"

from ._common import *
from .core import cmd_run
from .core import cmd_benchmark
from .core import cmd_compare
from .core import cmd_loadtest
from .core import cmd_stress
from .core import cmd_throughput
from .core import cmd_test_all
from .core import cmd_simulate
from .core import cmd_core
from .core import cmd_demo
from .security import cmd_security
from .security import cmd_keys
from .security import cmd_audit
from .security import cmd_deadletter
from .security import cmd_plugins
from .operations import cmd_serve
from .operations import cmd_chaos
from .operations import cmd_status
from .operations import cmd_query
from .operations import cmd_replay
from .operations import cmd_failover
from .operations import cmd_archive
from .operations import cmd_retention
from .operations import cmd_daemon
from .operations import cmd_gateway
from .operations import cmd_config
from .operations import cmd_doctor
from .operations import cmd_completion
from .market import cmd_historical
from .market import cmd_analytics
from .market import cmd_watchdog
from .market import cmd_bbo
from .market import cmd_live
from .market import cmd_feed
from .market import cmd_chart
from .market import cmd_depth
from .market import cmd_vwap
from .market import cmd_export
from .market import cmd_columnar
from .market import cmd_mbo
from .market import cmd_arbitrate
from .market import cmd_tca
from .market import cmd_report
from .market import cmd_flow
from .integrations import cmd_sub
from .integrations import cmd_top
from .integrations import cmd_strategy
from .integrations import cmd_itch
from .integrations import cmd_edgar
from .integrations import cmd_vessel
from .integrations import cmd_dashboard
from .integrations import cmd_sdk_demo
from .integrations import cmd_desk


class MDRAPArgumentParser(argparse.ArgumentParser):
    """
    Enhanced ArgumentParser with concise, targeted error reporting,
    fuzzy typo suggestions, transparent mnemonic alias routing, and suppressed multi-page usage dumps.
    """

    def parse_known_args(self, args=None, namespace=None):
        if args is None:
            args = sys.argv[1:]
        else:
            args = list(args)
        # Only map top-level verbs on the root parser (prog has no space), not subparser options
        if " " not in (self.prog or "") and "MNEMONIC_MAP" in globals():
            m_map = globals()["MNEMONIC_MAP"]
            for i, a in enumerate(args):
                if not a.startswith("-"):
                    cmd = a.lower()
                    if cmd in m_map:
                        args[i] = m_map[cmd]
                    break
        return super().parse_known_args(args, namespace)

    def error(self, message: str):
        console = Console(stderr=True)
        prog_name = self.prog.split()[-1] if self.prog else "mdrap"

        # 1. Fuzzy match on invalid choices
        m_choice = re.search(
            r"invalid choice:\s*'([^']+)'\s*\(choose from\s*([^)]+)\)", message
        )
        if m_choice:
            bad_val = m_choice.group(1)
            valid_choices = [
                c.strip().strip("'\"") for c in m_choice.group(2).split(",")
            ]
            console.print(
                f"\n[bold red]Error in '{prog_name}':[/bold red] unrecognized command or choice '[bold yellow]{bad_val}[/bold yellow]'"
            )
            matches = difflib.get_close_matches(bad_val, valid_choices, n=2, cutoff=0.5)
            if matches:
                console.print(
                    f"  [bold green]Did you mean:[/bold green] [bold cyan]{matches[0]}[/bold cyan]?"
                )
            elif len(valid_choices) > 20:
                console.print("  [dim]Available command categories:[/dim]\n")
                render_command_palette(console)
            else:
                console.print(
                    f"  [dim]Available choices:[/dim] {', '.join(valid_choices)}"
                )

        # 2. Unrecognized arguments
        elif "unrecognized arguments:" in message:
            unrec = message.split("unrecognized arguments:", 1)[1].strip()
            console.print(
                f"  [yellow]Unexpected argument(s):[/yellow] [bold red]{unrec}[/bold red]"
            )
            if prog_name in ("edgar", "research"):
                console.print(
                    "  [dim]Supported actions:[/dim] [cyan]events, filings, insiders, profile, facts[/cyan]"
                )
                console.print(
                    "  [dim]Correct syntax:[/dim] [green]mdrap edgar filings <TICKER> -l 5[/green]  or  [green]mdrap edgar <TICKER>[/green]"
                )
            elif prog_name in ("options", "opt"):
                console.print(
                    "  [dim]Supported actions:[/dim] [cyan]price, chain[/cyan]"
                )
                console.print(
                    "  [dim]Correct syntax:[/dim] [green]mdrap options price -u AAPL -s 150 -k 150 -e 30[/green]"
                )
            elif prog_name in ("backtest", "bt"):
                console.print(
                    "  [dim]Correct syntax:[/dim] [green]mdrap backtest -s whale_momentum -i AAPL[/green]"
                )
            elif prog_name in ("depth", "l2", "book"):
                console.print(
                    "  [dim]Correct syntax:[/dim] [green]mdrap depth <SYMBOL>[/green]  (e.g. mdrap depth AAPL)"
                )
            elif prog_name in ("flow", "cvd"):
                console.print(
                    "  [dim]Correct syntax:[/dim] [green]mdrap flow <SYMBOL>[/green]  (e.g. mdrap flow AAPL)"
                )
            elif prog_name in ("news", "sentiment"):
                console.print(
                    "  [dim]Supported actions:[/dim] [cyan]latest, analyze, summary, fetch[/cyan]"
                )
                console.print(
                    '  [dim]Correct syntax:[/dim] [green]mdrap news latest -s <TICKER>[/green]  or  [green]mdrap news <TICKER>[/green]  or  [green]mdrap news analyze "<TEXT>"[/green]'
                )

        # 3. Print concise usage, suppressing the giant multi-command wall
        usage_str = self.format_usage().strip()
        if len(usage_str) > 120 and "{" in usage_str:
            console.print(
                "  [dim]Run [cyan]mdrap --help[/cyan] or [cyan]mdrap status[/cyan] to view available commands.[/dim]\n"
            )
        else:
            console.print(f"  [dim]{usage_str}[/dim]\n")

        sys.exit(2)


def _add_fastpath_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "-f",
        "--fastpath",
        dest="fastpath",
        action="store_true",
        default=True,
        help="Enable Native C hot path accelerator (default: enabled)",
    )
    p.add_argument(
        "--no-fastpath",
        dest="fastpath",
        action="store_false",
        help="Disable Native C accelerator and use pure Python",
    )


def _add_limit_arg(
    p: argparse.ArgumentParser, default: int = 20, help_text: str = "Row limit"
) -> None:
    p.add_argument("-l", "--limit", type=int, default=default, help=help_text)


def _add_symbol_arg(
    p: argparse.ArgumentParser, default: str = "AAPL", help_text: str | None = None
) -> None:
    p.add_argument(
        "symbol",
        nargs="?",
        default=default,
        help=help_text or f"Symbol (default: {default})",
    )


def _add_events_arg(
    p: argparse.ArgumentParser, default: int = 50_000, help_text: str | None = None
) -> None:
    p.add_argument(
        "-e",
        "--events",
        type=int,
        default=default,
        help=help_text or f"Number of events (default: {default:,})",
    )


def _add_seed_arg(p: argparse.ArgumentParser, default: int = 42) -> None:
    p.add_argument(
        "-s",
        "--seed",
        type=int,
        default=default,
        help="Deterministic random seed",
    )


def _add_host_port_args(
    p: argparse.ArgumentParser,
    default_host: str = "127.0.0.1",
    default_port: int = 9876,
) -> None:
    p.add_argument(
        "--host",
        default=default_host,
        help=f"Listening host (default: {default_host})",
    )
    p.add_argument(
        "-p",
        "--port",
        type=int,
        default=default_port,
        help=f"Listening port (default: {default_port})",
    )


def _add_export_report_args(
    p: argparse.ArgumentParser, report_name: str = "report"
) -> None:
    p.add_argument(
        "--export",
        nargs="?",
        const=True,
        default=None,
        help=f"Export {report_name} (.xlsx)",
    )
    p.add_argument(
        "--open",
        action="store_true",
        help="Open exported report in Microsoft Excel (Windows only)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = MDRAPArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output structured JSON instead of formatted tables",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Force rich terminal UI formatting even in non-interactive/redirected contexts",
    )
    parser.add_argument(
        "--no-color",
        "--plain",
        action="store_true",
        dest="no_color",
        help="Suppress all ANSI color and styling (honors NO_COLOR=1)",
    )
    sub = parser.add_subparsers(
        dest="command", required=False, parser_class=MDRAPArgumentParser
    )

    def _sub(
        name: str,
        func,
        help_text: str,
        aliases: list[str] | None = None,
        db: bool = False,
        default_db: str = "data/mdrap.db",
    ):
        kw = {"help": help_text}
        if aliases:
            kw["aliases"] = aliases
        p = sub.add_parser(name, **kw)
        if db:
            p.add_argument(
                "--db",
                default=default_db,
                help="Database path" if default_db != ":memory:" else None,
            )
        p.add_argument(
            "--json",
            action="store_true",
            default=argparse.SUPPRESS,
            help="Output structured JSON instead of formatted tables",
        )
        p.add_argument(
            "--pretty",
            action="store_true",
            default=argparse.SUPPRESS,
            help="Force rich terminal UI formatting even in non-interactive/redirected contexts",
        )
        p.add_argument(
            "--no-color",
            "--plain",
            action="store_true",
            dest="no_color",
            default=argparse.SUPPRESS,
            help="Suppress all ANSI color and styling (honors NO_COLOR=1)",
        )
        p.set_defaults(func=func)
        return p

    _sub(
        "desk",
        cmd_desk,
        "Launch interactive keyboard-first modal desk navigator (Vim/Excel ergonomics)",
        ["nav"],
    )
    _sub(
        "status",
        cmd_status,
        "Show comprehensive platform status overview",
        ["s"],
        db=True,
    )
    _sub(
        "shell",
        lambda args: cmd_shell(args, parser),
        "Launch low-latency interactive slash-command shell",
        ["sh"],
        db=True,
    )

    # Run pipeline
    p_run = _sub(
        "run",
        cmd_run,
        "Run the pipeline against the simulator (optionally with live dashboard)",
        ["r"],
        db=True,
    )
    _add_sim_flags(p_run, default_events=50_000)
    p_run.add_argument(
        "-v",
        "--version",
        choices=["v1", "v2"],
        default="v1",
        help="Pipeline version (v1: sync, v2: streaming)",
    )
    _add_fastpath_args(p_run)
    p_run.add_argument(
        "-a",
        "--archive",
        action="store_true",
        help="Enable immutable raw event archiving to data/raw_archive/",
    )
    p_run.add_argument(
        "--no-analytics",
        dest="analytics",
        action="store_false",
        help="Disable V3 analytics aggregation",
    )
    p_run.add_argument(
        "-d",
        "--dashboard",
        action="store_true",
        help="Show live rich terminal dashboard",
    )
    p_run.add_argument(
        "--strict-sync",
        action="store_true",
        default=False,
        help="Exit 1 if secondary DuckDB sync fails (prevents silent store divergence in automation)",
    )
    p_run.add_argument(
        "--no-sync",
        action="store_true",
        default=False,
        help="Skip automatic DuckDB columnar store sync at run completion",
    )

    # Benchmark
    p_bench = _sub(
        "benchmark",
        cmd_benchmark,
        "Run controlled benchmark and score quality detection",
        ["bench"],
        db=True,
        default_db=":memory:",
    )
    _add_sim_flags(p_bench, default_events=500_000)
    p_bench.add_argument(
        "-v", "--version", choices=["v1", "v2"], default="v1", help="Pipeline version"
    )
    _add_fastpath_args(p_bench)
    p_bench.add_argument("-w", "--warmup", type=int, default=5000, help="Warmup events")
    p_bench.add_argument("-l", "--label", default="baseline", help="Benchmark label")
    p_bench.add_argument(
        "-o", "--out-dir", default="benchmarks", help="Output directory for results"
    )
    p_bench.add_argument(
        "-p",
        "--profile",
        action="store_true",
        help="Profile with cProfile and dump stats",
    )

    # Architectural comparison
    p_compare = _sub(
        "compare",
        cmd_compare,
        "Run V1 Pure Python vs V1 Native C on identical workloads and compare",
        ["comp"],
        db=True,
        default_db=":memory:",
    )
    _add_sim_flags(p_compare, default_events=100_000)
    p_compare.add_argument(
        "-w", "--warmup", type=int, default=2000, help="Warmup events"
    )

    # Load test
    p_load = _sub(
        "loadtest",
        cmd_loadtest,
        "Sweep increasing event volumes and report trend",
        ["load"],
    )
    p_load.add_argument(
        "--levels",
        default="10000,50000,100000,250000,500000",
        help="Comma-separated event counts",
    )
    _add_seed_arg(p_load)
    p_load.add_argument("-o", "--out-dir", default="benchmarks")

    # Chaos drill (§15)
    p_chaos = _sub(
        "chaos", cmd_chaos, "Execute automated chaos & resilience drills (§15)", ["ch"]
    )
    p_chaos.add_argument(
        "drill",
        nargs="?",
        default="all",
        choices=["feed", "jitter", "burst", "storage", "all", "kill"],
        help="Chaos drill type",
    )
    _add_events_arg(p_chaos, default=50_000)
    _add_seed_arg(p_chaos)
    p_chaos.add_argument("--kill-source", default="FEEDX", help="Source to drop")
    p_chaos.add_argument(
        "--kill-start", type=int, default=0, help="Drop begins after this many events"
    )
    p_chaos.add_argument(
        "--kill-duration", type=int, default=500, help="Number of events to drop"
    )

    # Security & RBAC (§19)
    _sub(
        "security",
        cmd_security,
        "Display platform security posture, HMAC verification, RBAC, and rate limiting status",
        ["sec"],
        db=True,
    )

    # API Keys & Client Authentication
    p_keys = _sub(
        "keys", cmd_keys, "Manage client API keys and authentication tokens", db=True
    )
    p_keys.add_argument(
        "action",
        nargs="?",
        default="list",
        choices=["list", "create", "revoke", "rotate"],
        help="Action to perform (default: list)",
    )
    p_keys.add_argument(
        "--client-id",
        default="Custom_Client",
        help="Client identifier name (for create)",
    )
    p_keys.add_argument(
        "--role",
        default="VIEWER",
        choices=["VIEWER", "OPERATOR", "ADMIN"],
        help="Role assignment: VIEWER, OPERATOR, or ADMIN (for create)",
    )
    p_keys.add_argument(
        "--rate", type=float, default=None, help="Custom rate limit eps"
    )
    p_keys.add_argument(
        "--token", default="", help="API key token (for revoke or rotate)"
    )
    p_keys.add_argument("--prefix", default="", help="API key prefix (for revoke)")
    p_keys.add_argument(
        "--grace",
        type=float,
        default=3600.0,
        help="Rotation grace period in seconds (default: 3600)",
    )

    # API & WebSocket Production Server
    p_serve = _sub(
        "serve",
        cmd_serve,
        "Start production REST API and WebSocket event streaming server",
        aliases=["api"],
        db=True,
    )
    p_serve.add_argument(
        "--host",
        default=os.getenv("MDRAP_HOST", "0.0.0.0"),
        help="Bind host (default: 0.0.0.0)",
    )
    p_serve.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("MDRAP_PORT", "8000")),
        help="Bind port (default: 8000)",
    )
    p_serve.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development",
    )

    # Tamper-Evident Audit Trail (§19)
    p_audit = _sub(
        "audit",
        cmd_audit,
        "View and cryptographically verify tamper-evident audit logs",
        db=True,
    )
    p_audit.add_argument(
        "--verify",
        action="store_true",
        help="Cryptographically verify SHA-256 Merkle chain integrity",
    )
    p_audit.add_argument(
        "--export-proof",
        metavar="FILE",
        help="Export cryptographic audit trail as an independently verifiable JSON proof",
    )
    p_audit.add_argument(
        "--verify-proof",
        metavar="FILE",
        help="Independently verify a standalone JSON audit proof without database access",
    )
    p_audit.add_argument(
        "--anchor-batch",
        action="store_true",
        help="Generate and anchor a Merkle root batch over recent audit log entries",
    )
    p_audit.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Batch size for Merkle audit root anchoring (default: 100)",
    )
    p_audit.add_argument(
        "--anchor",
        metavar="COUNT:HEAD",
        help="Verify audit trail against an external anchor (format: <count>:<head_hash>)",
    )
    p_audit.add_argument(
        "--print-anchor",
        action="store_true",
        help="Print current audit anchor (<count>:<head_hash>) for external witnessing",
    )
    p_audit.add_argument(
        "--sign-checkpoint",
        action="store_true",
        help="Generate HMAC-SHA256 signed audit checkpoint using MDRAP_AUDIT_KEY",
    )
    _add_limit_arg(p_audit, default=20, help_text="Number of audit records to show")

    # Dead-Letter Spill & Replay
    p_deadletter = _sub(
        "deadletter",
        cmd_deadletter,
        "Inspect and replay uncommitted dead-letter transaction logs",
        db=True,
    )
    p_deadletter.add_argument(
        "action",
        nargs="?",
        default="list",
        choices=["list", "replay"],
        help="Action: list pending files or replay into database",
    )
    p_deadletter.add_argument(
        "--dir",
        metavar="PATH",
        help="Custom dead-letter spill directory path",
    )

    # Installed Plugins & Extension Points
    _sub(
        "plugins",
        cmd_plugins,
        "List installed plugins and extension entry points",
    )

    # Query
    p_query = _sub(
        "query",
        cmd_query,
        "Inspect stored data: health, latest, lineage, quarantine",
        ["q"],
        db=True,
    )
    p_query.add_argument(
        "action",
        nargs="?",
        default=None,
        help="Action: health, latest, lineage, quarantine, counts",
    )
    p_query.add_argument(
        "target",
        nargs="?",
        default=None,
        help="Target symbol, event ID, or sample count",
    )
    p_query.add_argument(
        "--latest", metavar="INSTRUMENT", help="Latest event for instrument"
    )
    _add_limit_arg(p_query, default=1, help_text="Row limit")
    p_query.add_argument("--lineage", metavar="EVENT_ID", help="Lineage for event ID")
    p_query.add_argument("--health", action="store_true", help="Feed health summary")
    p_query.add_argument(
        "--quarantine",
        nargs="?",
        const=10,
        type=int,
        default=None,
        metavar="N",
        help="Quarantine sample",
    )

    # Historical providers and archive commands
    from ..chd import add_historical_parser

    add_historical_parser(sub)

    p_replay = _sub(
        "replay",
        cmd_replay,
        "Replay archived raw events through the pipeline",
        ["rep"],
        db=True,
        default_db="data/mdrap_replay.db",
    )
    p_replay.add_argument(
        "--base-dir", default="data/raw_archive", help="Archive directory"
    )
    p_replay.add_argument(
        "-d", "--date", default=None, help="Replay only a specific date (YYYY-MM-DD)"
    )
    p_replay.add_argument(
        "-s", "--source", default=None, help="Replay only a specific source"
    )
    p_replay.add_argument(
        "--speed",
        type=float,
        default=None,
        help="Replay speed factor (1.0=realtime, 10.0=10x, 0=unthrottled)",
    )
    p_replay.add_argument(
        "--symbol", default=None, help="Filter replay by instrument symbol"
    )
    p_replay.add_argument(
        "--limit", type=int, default=None, help="Limit number of replayed events"
    )
    p_replay.add_argument(
        "--from-sqlite",
        default=None,
        help="Load replay events from SQLite database path",
    )
    p_replay.add_argument(
        "--from-journal",
        default=None,
        help="Load replay events from binary journal (.dbn) path",
    )

    p_archive = _sub(
        "archive", cmd_archive, "Show raw event archive statistics", ["arc"]
    )
    p_archive.add_argument(
        "--base-dir", default="data/raw_archive", help="Archive directory"
    )

    p_ret = _sub(
        "retention",
        cmd_retention,
        "Run storage retention compaction and disk reclamation",
        ["prune"],
        db=True,
    )
    p_ret.add_argument(
        "--days",
        type=int,
        default=30,
        help="Retention window for canonical events in days (default: 30)",
    )
    p_ret.add_argument(
        "--quarantine-days",
        type=int,
        default=90,
        help="Retention window for quarantine records in days (default: 90)",
    )
    p_ret.add_argument(
        "--vacuum",
        action="store_true",
        help="Execute full SQLite VACUUM to reclaim filesystem disk space",
    )

    p_failover = _sub(
        "failover",
        cmd_failover,
        "Active-Passive cluster failover coordinator",
        ["fo"],
    )
    p_failover.add_argument(
        "action",
        nargs="?",
        default="status",
        choices=["status", "promote", "demote", "heartbeat"],
        help="Failover command action",
    )
    p_failover.add_argument("--node-id", default="node-local", help="Cluster node ID")
    p_failover.add_argument("--cluster", default="mdrap-cluster", help="Cluster name")
    p_failover.add_argument(
        "--reason", default="Manual operator request", help="Transition reason"
    )
    p_failover.add_argument(
        "--seq", type=int, default=0, help="Heartbeat sequence number"
    )

    p_lake = _sub(
        "lake",
        cmd_historical,
        "Manage partitioned historical market data store",
        ["partitions", "store-hist"],
    )
    p_lake.add_argument(
        "action",
        nargs="?",
        default="catalog",
        choices=["catalog", "partition", "query", "retention"],
        help="Historical store action",
    )
    p_lake.add_argument(
        "--base-dir",
        default="data/historical",
        help="Historical base storage directory",
    )
    p_lake.add_argument(
        "--from-db", default="data/mdrap.db", help="Source SQLite DB for partitioning"
    )
    p_lake.add_argument(
        "--format",
        default="auto",
        choices=["auto", "jsonl.gz", "csv.gz", "parquet"],
        help="Partition storage format",
    )
    p_lake.add_argument("--symbol", default=None, help="Filter by symbol")
    p_lake.add_argument("--limit", type=int, default=20, help="Query row limit")
    p_lake.add_argument(
        "--days", type=int, default=30, help="Retention max age in days"
    )
    p_lake.add_argument(
        "--dry-run", action="store_true", help="Retention dry run without deletion"
    )

    # V3: Analytics commands
    p_analytics = _sub(
        "analytics",
        cmd_analytics,
        "Query OHLCV candles, bid-ask spreads, and realized volatility",
        ["a"],
        db=True,
    )
    p_analytics.add_argument(
        "action", nargs="?", default=None, help="Action: ohlcv, spread, vol, summary"
    )
    p_analytics.add_argument(
        "target",
        nargs="?",
        default=None,
        help="Instrument symbol (e.g. AAPL, MSFT, all)",
    )
    p_analytics.add_argument(
        "--ohlcv", metavar="INSTRUMENT", help="Show OHLCV candles for an instrument"
    )
    p_analytics.add_argument(
        "--spread",
        metavar="INSTRUMENT",
        help="Show bid-ask spread analysis (use 'all' for all instruments)",
    )
    p_analytics.add_argument(
        "--volatility",
        action="store_true",
        help="Show realized volatility by instrument",
    )
    p_analytics.add_argument(
        "--summary", action="store_true", help="Show market analytics summary"
    )
    _add_limit_arg(p_analytics, default=20, help_text="Row limit")

    # Synthetic Consolidated BBO
    p_bbo = _sub(
        "bbo",
        cmd_bbo,
        "Query Synthetic Consolidated Best Bid & Offer (NBBO)",
        ["nbbo"],
        db=True,
    )
    _add_symbol_arg(
        p_bbo, default=None, help_text="Instrument symbol (e.g. AAPL or 'all')"
    )

    # Live market streaming & in-place ticker dashboard
    p_live = _sub(
        "live",
        cmd_live,
        "Stream live market ticks with in-place updating table & candlestick chart",
        ["stream"],
        db=True,
    )
    _add_symbol_arg(
        p_live,
        default="BTC/USD",
        help_text="Symbol to stream (e.g. BTC/USD, AAPL, or 'all')",
    )
    _add_limit_arg(
        p_live,
        default=20,
        help_text="Number of ticks to stream (default 20, 0 for continuous)",
    )
    p_live.add_argument(
        "--fast",
        action="store_true",
        help="High-speed streaming mode (10ms poll interval, high-frequency terminal updates)",
    )
    p_live.add_argument(
        "--poll-ms",
        type=float,
        default=None,
        help="Polling interval in milliseconds (e.g. --poll-ms 10 for 10ms)",
    )
    p_live.add_argument(
        "--ws",
        action="store_true",
        help="Stream using true real-time WebSockets (<1ms push) instead of HTTP polling",
    )
    p_live.add_argument(
        "--sim",
        action="store_true",
        help="Use realistic multi-venue simulator stream instead of public internet API",
    )
    p_live.add_argument(
        "--feed",
        choices=["crypto", "polygon", "poly", "databento", "dbn", "sim"],
        default=None,
        help="Streaming feed source provider",
    )
    p_live.add_argument(
        "--mock-feed",
        action="store_true",
        help="Run provider in high-fidelity wire-format mock generator mode",
    )
    p_live.add_argument(
        "--polygon-key",
        default=None,
        help="Polygon.io API key (or set POLYGON_API_KEY env var)",
    )
    p_live.add_argument(
        "--databento-key",
        default=None,
        help="Databento API key (or set DATABENTO_API_KEY env var)",
    )
    p_live.add_argument(
        "--dbn-file", default=None, help="Path to historical .dbn binary file to stream"
    )

    # Phase 2: Direct High-Throughput Streaming Feed Inspector
    p_feed = _sub(
        "feed",
        cmd_feed,
        "Inspect, benchmark, and test streaming feeds (Polygon, Databento, Crypto WS)",
        ["feeds"],
    )
    p_feed.add_argument(
        "--source",
        choices=["polygon", "databento", "crypto", "all"],
        default="databento",
        help="Streaming feed source",
    )
    p_feed.add_argument(
        "--symbols", default="AAPL,MSFT,NVDA", help="Comma-separated symbols to stream"
    )
    p_feed.add_argument(
        "-c", "--count", type=int, default=50, help="Number of packets to ingest"
    )
    p_feed.add_argument(
        "--mock",
        action="store_true",
        default=True,
        help="Use high-fidelity wire-format mock stream",
    )
    p_feed.add_argument("--key", default=None, help="API key for feed provider")

    # In-Terminal Candlestick Chart & Volume Graph
    p_chart = _sub(
        "chart",
        cmd_chart,
        "Display visual in-terminal ASCII/Unicode candlestick chart",
        ["candle"],
        db=True,
    )
    _add_symbol_arg(
        p_chart, default="AAPL", help_text="Symbol to chart (e.g. AAPL, BTC/USD)"
    )
    p_chart.add_argument(
        "-i",
        "--interval",
        default="5s",
        help="Candlestick timeframe interval (e.g. 1s, 5s, 1m, 15m, 1h, default 5s)",
    )
    p_chart.add_argument(
        "-w",
        "--width",
        type=int,
        default=56,
        help="Chart width in characters (default 56)",
    )
    p_chart.add_argument(
        "-H",
        "--height",
        type=int,
        default=10,
        help="Chart height in lines (default 10)",
    )
    p_chart.add_argument(
        "--duckdb", default="data/mdrap.duckdb", help="Path to DuckDB database"
    )
    p_chart.add_argument(
        "--sim",
        action="store_true",
        help="Simulate trade stream if no stored candles found",
    )

    # Consolidated Level-2 Market Depth
    p_depth = _sub(
        "depth",
        cmd_depth,
        "Show Consolidated Level-2 Multi-Venue Market Depth Ladder",
        ["l2"],
        db=True,
    )
    _add_symbol_arg(
        p_depth, default="BTC/USD", help_text="Symbol to inspect (e.g. BTC/USD)"
    )
    _add_limit_arg(
        p_depth, default=10, help_text="Number of depth levels per side (default 10)"
    )

    # Phase E: Multi-Venue VWAP Execution & Slippage Curves
    p_vwap = _sub(
        "vwap",
        cmd_vwap,
        "Compute multi-venue real-time VWAP execution & slippage curves",
        ["curve"],
        db=True,
    )
    _add_symbol_arg(
        p_vwap, default="BTC/USD", help_text="Symbol to inspect (e.g. BTC/USD)"
    )
    p_vwap.add_argument(
        "--sizes",
        nargs="+",
        type=float,
        default=[1.0, 5.0, 10.0, 25.0, 50.0],
        help="Order sizing tranches (default: 1 5 10 25 50)",
    )

    # Phase G: Institutional Financial Report & Model Exporter (Excel / CSV)
    p_export = _sub(
        "export",
        cmd_export,
        "Export market microstructure data to Excel (.xlsx) or CSV",
        ["exp"],
        db=True,
    )
    _add_symbol_arg(
        p_export, default="AAPL", help_text="Symbol to export (default: AAPL)"
    )
    p_export.add_argument(
        "-o", "--output", default=None, help="Custom output file or directory path"
    )
    p_export.add_argument(
        "--outdir",
        default="data/reports",
        help="Directory for exported reports (default: data/reports)",
    )
    p_export.add_argument(
        "--csv",
        action="store_true",
        help="Export as structured CSV package instead of Excel (.xlsx)",
    )
    p_export.add_argument(
        "--format",
        choices=["excel", "csv", "parquet", "json"],
        default=None,
        help="Export format: excel, csv, parquet, json",
    )
    p_export.add_argument(
        "--table",
        default="canonical_events",
        help="Database table to export (default: canonical_events)",
    )
    p_export.add_argument(
        "--open",
        action="store_true",
        help="Automatically launch generated workbook in Excel (Windows only)",
    )

    # Phase 7: Watchdog commands
    p_watchdog = _sub(
        "watchdog",
        cmd_watchdog,
        "Show source health status and watchdog alerts",
        ["w"],
        db=True,
    )
    p_watchdog.add_argument(
        "action", nargs="?", default=None, help="Action: status or alerts"
    )
    p_watchdog.add_argument("target", nargs="?", default=None, help="Alert limit count")
    p_watchdog.add_argument(
        "--status", action="store_true", help="Show current source health status"
    )
    p_watchdog.add_argument(
        "-a",
        "--alerts",
        nargs="?",
        const=10,
        type=int,
        default=None,
        metavar="N",
        help="Show recent watchdog alerts",
    )
    _add_limit_arg(p_watchdog, default=10, help_text="Alert count limit")

    # Phase 10 / Market Service: Headless Streaming Daemon & Subscriber Client (§18)
    p_daemon = _sub(
        "daemon",
        cmd_daemon,
        "Run headless streaming socket daemon service (§18)",
        ["d"],
        db=True,
    )
    _add_host_port_args(p_daemon, default_host="127.0.0.1", default_port=9876)
    p_daemon.add_argument(
        "--live",
        action="store_true",
        help="Ingest real-time Binance & Coinbase market feeds",
    )
    _add_events_arg(
        p_daemon, default=0, help_text="Event limit (0 for infinite continuous stream)"
    )
    p_daemon.add_argument(
        "--speed", type=float, default=1000.0, help="Simulated events per second"
    )
    p_daemon.add_argument(
        "--token",
        default="",
        help="Pre-shared bearer authentication token for multi-user security",
    )
    p_daemon.add_argument(
        "--no-shm",
        action="store_true",
        help="Disable zero-copy shared memory publisher",
    )
    p_daemon.add_argument(
        "--shm-name",
        default="mdrap_feed",
        help="Shared memory segment name (default mdrap_feed)",
    )

    p_sub = _sub(
        "sub",
        cmd_sub,
        "Subscribe to daemon stream and output ticks or depth to stdout",
        ["subscribe"],
    )
    _add_symbol_arg(
        p_sub, default="ALL", help_text="Symbol to stream (e.g. BTC/USD, AAPL, or ALL)"
    )
    _add_host_port_args(p_sub, default_host="127.0.0.1", default_port=9876)
    _add_limit_arg(
        p_sub, default=0, help_text="Limit number of ticks (0 for continuous)"
    )
    p_sub.add_argument(
        "--l2",
        action="store_true",
        help="Subscribe to Consolidated Level-2 Depth ladders",
    )
    p_sub.add_argument(
        "--vwap",
        action="store_true",
        help="Subscribe to real-time institutional VWAP curves",
    )
    p_sub.add_argument(
        "--shm",
        action="store_true",
        help="Read directly from zero-copy shared memory buffer (<1µs latency)",
    )
    p_sub.add_argument(
        "--shm-name",
        default="mdrap_feed",
        help="Shared memory segment name (default mdrap_feed)",
    )
    p_sub.add_argument(
        "--binary",
        action="store_true",
        help="Stream using fixed-width binary protocol (MDRAP-BIN V1, ~75% smaller, <2µs)",
    )
    p_sub.add_argument(
        "-j",
        dest="json",
        action="store_true",
        help="Output raw JSON for piping into jq or trading bots",
    )
    p_sub.add_argument(
        "--token", default="", help="Pre-shared bearer authentication token"
    )

    p_top = _sub(
        "top",
        cmd_top,
        "Launch dynamic full-screen terminal service cockpit",
        ["mon"],
    )
    _add_host_port_args(p_top, default_host="127.0.0.1", default_port=9876)
    p_top.add_argument(
        "--token", default="", help="Pre-shared bearer authentication token"
    )

    # Multi-directional stress testing & scale analyzer
    p_stress = _sub(
        "stress",
        cmd_stress,
        "Run multi-directional stress tests and 1M to 1B scale analysis",
        ["str"],
    )
    p_stress.add_argument(
        "--module",
        choices=[
            "all",
            "gateway",
            "quality",
            "bbo",
            "storage",
            "ipc",
            "e2e",
            "adversarial",
        ],
        default="all",
        help="Target module to stress",
    )
    _add_events_arg(
        p_stress, default=25000, help_text="Number of stress events (default 25,000)"
    )

    # Comprehensive test runner
    p_test_all = _sub(
        "test-all",
        cmd_test_all,
        "Run all CLI tests, benchmarks, queries, and validations in one place",
        ["t"],
        db=True,
        default_db="data/mdrap_test.db",
    )
    p_test_all.add_argument(
        "--duckdb", default="data/mdrap_test.duckdb", help="Path to DuckDB database"
    )
    p_test_all.add_argument("-s", "--seed", type=int, default=42)

    # Version
    _sub(
        "version",
        lambda args: (
            print(json.dumps({"version": __version__, "platform": "MDRAP"}, indent=2))
            if getattr(args, "json", False)
            else print(f"MDRAP v{__version__}")
        ),
        "Show MDRAP version",
        ["v"],
    )

    # Phase 3: DuckDB Columnar Time-Series Storage & Vectorized Analytics
    p_col = _sub(
        "columnar",
        cmd_columnar,
        "Query high-performance DuckDB columnar time-series storage & analytics (Phase 3)",
        ["col"],
        db=True,
    )
    p_col.add_argument(
        "action",
        nargs="?",
        default="info",
        help="Columnar operation (sync, ohlcv, vwap, spread, latency, profile, export, bench, sql, info)",
    )
    p_col.add_argument(
        "target",
        nargs="?",
        default=None,
        help="Target symbol, SQL query, or export output path",
    )
    p_col.add_argument(
        "--duckdb",
        default="data/mdrap.duckdb",
        help="Path to DuckDB database file (default: data/mdrap.duckdb)",
    )
    p_col.add_argument(
        "-i",
        "--interval",
        type=float,
        default=5.0,
        help="Resampling interval in seconds for OHLCV (default: 5.0)",
    )
    p_col.add_argument(
        "-l", "--limit", type=int, default=20, help="Max rows to return (default: 20)"
    )
    p_col.add_argument(
        "--bins",
        type=int,
        default=15,
        help="Number of price bins for volume profile (default: 15)",
    )
    p_col.add_argument(
        "-o", "--output", default=None, help="Parquet export output path"
    )
    p_col.add_argument(
        "--compression",
        choices=["zstd", "snappy", "gzip"],
        default="zstd",
        help="Parquet compression codec (default: zstd)",
    )
    p_col.add_argument(
        "--full",
        action="store_true",
        help="Force full SQLite table re-scan during sync instead of incremental CDC",
    )

    # Multi-Device Workload Simulation (§26)
    p_sim = _sub(
        "simulate",
        cmd_simulate,
        "Simulate concurrent multi-device normal vs fast-paced user workloads (§26)",
        ["sim"],
        db=True,
    )
    p_sim.add_argument(
        "--scale",
        choices=["pilot", "desk", "floor", "surge", "sweep", "custom"],
        default="desk",
        help="Simulation scale tier (default: desk)",
    )
    p_sim.add_argument(
        "-t",
        "--duration",
        type=float,
        default=5.0,
        help="Simulation duration in seconds (default: 5.0)",
    )
    p_sim.add_argument(
        "--normal",
        type=int,
        default=3,
        help="Number of normal user devices (for custom scale)",
    )
    p_sim.add_argument(
        "--fast",
        type=int,
        default=3,
        help="Number of fast-paced bot devices (for custom scale)",
    )
    p_sim.add_argument(
        "--monitor",
        type=int,
        default=0,
        help="Number of DevOps monitor devices (for custom scale)",
    )
    p_sim.add_argument(
        "--mode",
        choices=["thread", "process"],
        default="thread",
        help="Worker concurrency mode (default: thread)",
    )
    p_sim.add_argument(
        "--port",
        type=int,
        default=19880,
        help="Streaming daemon TCP port (default: 19880)",
    )
    p_sim.add_argument(
        "--prom-port",
        type=int,
        default=19110,
        help="Prometheus HTTP port (default: 19110)",
    )
    p_sim.add_argument(
        "--eps",
        type=float,
        default=3000.0,
        help="Simulated feed tick generation rate (default: 3000.0)",
    )
    p_sim.add_argument(
        "--duckdb", default="data/mdrap.duckdb", help="Path to DuckDB database"
    )
    p_sim.add_argument(
        "-o", "--report", default=None, help="Save JSON performance report to file"
    )

    # Phase 5: Market-By-Order (L3 MBO) Engine
    p_mbo = _sub(
        "mbo",
        cmd_mbo,
        "Inspect Level-3 Market-By-Order (MBO) FIFO queue ranks and L2 book projection (§18, §26)",
        ["l3"],
    )
    p_mbo.add_argument(
        "symbol", nargs="?", default="AAPL", help="Symbol to inspect (default: AAPL)"
    )
    p_mbo.add_argument(
        "-l",
        "--limit",
        type=int,
        default=5,
        help="Depth levels to display (default: 5)",
    )

    # Phase 6: Multicast UDP A/B Arbitrator & Gap Recovery
    p_arb = _sub(
        "arbitrate",
        cmd_arbitrate,
        "Run dual-path Multicast UDP A/B feed arbitration and TCP replay test (§18, §26)",
        ["arb"],
    )
    _add_events_arg(
        p_arb,
        default=500,
        help_text="Number of dual-line events to simulate (default: 500)",
    )
    p_arb.add_argument(
        "--drop-a",
        type=float,
        default=0.05,
        help="Packet drop rate on Feed A (default: 0.05)",
    )
    p_arb.add_argument(
        "--drop-b",
        type=float,
        default=0.05,
        help="Packet drop rate on Feed B (default: 0.05)",
    )

    # Phase 26: High-Throughput Native C SBE Validation Engine (§26)
    p_tp = _sub(
        "throughput",
        cmd_throughput,
        "Benchmark 500,000 to 1,000,000+ events/sec on vectorized Native C SBE stream (§26)",
        ["tp"],
    )
    _add_events_arg(
        p_tp,
        default=1_000_000,
        help_text="Number of events to benchmark (e.g. 500000 or 1000000, default: 1,000,000)",
    )
    p_tp.add_argument(
        "--anomalies",
        type=float,
        default=0.01,
        help="Anomaly injection rate (default: 0.01 = 1%%)",
    )
    p_tp.add_argument(
        "--compare",
        action="store_true",
        help="Display architectural progression comparison table",
    )

    # Institutional Best Execution & TCA Slippage Engine (§26, SEC 605/606)
    p_tca = _sub(
        "tca",
        cmd_tca,
        "Run Institutional Best Execution & TCA Slippage Engine with Merkle Proofs",
        ["bestex"],
        db=True,
    )
    _add_symbol_arg(
        p_tca, default="AAPL", help_text="Instrument symbol (default: AAPL)"
    )
    p_tca.add_argument(
        "-c",
        "--count",
        type=int,
        default=50,
        help="Number of demo execution records to generate (default: 50)",
    )
    p_tca.add_argument(
        "-f", "--file", default=None, help="Path to execution records CSV file"
    )
    _add_seed_arg(p_tca, default=42)
    p_tca.add_argument(
        "--demo",
        action="store_true",
        default=True,
        help="Run with realistic multi-broker demo dataset",
    )
    p_tca.add_argument(
        "--benchmark",
        choices=["ARRIVAL_PRICE", "MIDPOINT", "VWAP"],
        default="ARRIVAL_PRICE",
        help="Benchmark price for slippage calculation",
    )
    _add_export_report_args(p_tca, "3-tab audit-grade Excel TCA report")

    # Institutional Fund Regulatory Compliance Reports (SEC 13F, MiFID II RTS 28)
    p_report = _sub(
        "report",
        cmd_report,
        "Generate institutional fund regulatory compliance reports (SEC 13F, MiFID II RTS 28)",
        ["reg"],
        db=True,
    )
    p_report.add_argument(
        "report_type",
        nargs="?",
        default="13f",
        choices=["13f", "rts28", "form13f", "holdings", "venues", "mifid2"],
        help="Report type: '13f' (SEC Form 13F Holdings) or 'rts28' (MiFID II Execution Venues)",
    )
    p_report.add_argument(
        "--symbol",
        default="AAPL",
        help="Target symbol for venue analysis (default: AAPL)",
    )
    _add_seed_arg(p_report, default=42)

    # Institutional Order Flow & Cumulative Volume Delta (CVD) Tracker (§26)
    p_flow = _sub(
        "flow",
        cmd_flow,
        "Track Institutional Order Flow, Lee-Ready Aggressor Side, CVD & MPID Net Deltas",
        ["cvd"],
        db=True,
    )
    _add_symbol_arg(
        p_flow, default="AAPL", help_text="Instrument symbol (default: AAPL)"
    )
    p_flow.add_argument(
        "-c",
        "--count",
        type=int,
        default=500,
        help="Number of trades to analyze (default: 500)",
    )
    _add_seed_arg(p_flow, default=42)
    p_flow.add_argument(
        "--whales",
        action="store_true",
        help="Display only whale blocks and institutional prints",
    )
    _add_export_report_args(p_flow, "3-tab Order Flow & CVD Excel report")

    # Institutional Algorithmic Strategy Engine & Paper EMS (§26)
    p_strat = _sub(
        "strategy",
        cmd_strategy,
        "Institutional Algorithmic Strategy Engine & Paper EMS (§26)",
        ["strat"],
        db=True,
    )
    p_strat.add_argument(
        "action",
        nargs="?",
        default="list",
        choices=["list", "run"],
        help="Action to perform (default: list)",
    )
    p_strat.add_argument(
        "-s",
        "--strategy",
        default="whale_momentum",
        choices=["whale_momentum", "spread_capture", "avellaneda_stoikov", "as_mm"],
        help="Strategy name",
    )
    p_strat.add_argument(
        "-i", "--symbol", default="AAPL", help="Instrument symbol (default: AAPL)"
    )
    _add_events_arg(
        p_strat,
        default=1000,
        help_text="Event count for paper simulation (default: 1000)",
    )
    p_strat.add_argument(
        "-b",
        "--book",
        "--show-book",
        action="store_true",
        help="Display Level-2 Order Book depth ladder at run completion",
    )
    p_strat.add_argument(
        "-x",
        "--executions",
        "--trades",
        action="store_true",
        help="Display detailed strategy execution ledger with arrival prices and slippage",
    )
    p_strat.add_argument(
        "--export",
        nargs="?",
        const="default",
        default=None,
        metavar="FILE",
        help="Export strategy execution log and order book history to JSON or CSV",
    )

    # Phase 9: External TCP Gateway
    p_gw = _sub(
        "gateway",
        cmd_gateway,
        "Launch AsyncIO TCP Gateway for external clients",
        ["gw"],
    )
    _add_host_port_args(p_gw, default_host="127.0.0.1", default_port=9000)

    # Phase 9: Python SDK Demo
    _sub("sdk-demo", cmd_sdk_demo, "Run Quant-Ready Python SDK Client Demo", ["sdk"])

    p_dash = _sub(
        "dashboard",
        cmd_dashboard,
        "Launch real-time terminal visualizer dashboard",
        ["dash"],
    )
    p_dash.add_argument("--port", type=int, default=9000, help="TCP Gateway port")

    # NASDAQ TotalView-ITCH 5.0 Binary Feed Engine & Global Benchmark
    p_itch = _sub(
        "itch",
        cmd_itch,
        "NASDAQ TotalView-ITCH 5.0 Binary Feed Engine & Global Benchmark",
        ["totalview"],
    )
    p_itch.add_argument(
        "action",
        nargs="?",
        default="bench",
        choices=["bench", "parse", "generate"],
        help="Action to perform (default: bench)",
    )
    p_itch.add_argument(
        "file",
        nargs="?",
        default=None,
        help="Path to .itch or .itch.gz file (for parse)",
    )
    _add_events_arg(
        p_itch,
        default=1_000_000,
        help_text="Number of messages (for bench/generate, default: 1,000,000)",
    )
    p_itch.add_argument(
        "-o",
        "--output",
        default="data/sample.itch",
        help="Output file path (for generate)",
    )
    _add_limit_arg(
        p_itch,
        default=50,
        help_text="Number of records to preview (for parse)",
    )

    # Phase 10: SEC EDGAR Alternative Data & Corporate Research Engine
    p_edgar = _sub(
        "edgar",
        cmd_edgar,
        "SEC EDGAR Alternative Data: 8-K material events, Form 4 insiders, GAAP facts",
        ["filings"],
    )
    p_edgar.add_argument(
        "action",
        nargs="?",
        default="events",
        choices=["events", "insiders", "profile", "facts", "filings"],
        help="Action to perform (default: events)",
    )
    p_edgar.add_argument(
        "ticker",
        nargs="?",
        default="AAPL",
        help="Company ticker symbol (default: AAPL)",
    )
    p_edgar.add_argument(
        "-t",
        "--type",
        dest="form_type",
        default=None,
        help="Filter by form type (e.g., 10-K, 10-Q, 8-K, 4)",
    )
    _add_limit_arg(
        p_edgar,
        default=15,
        help_text="Maximum number of items to display (default: 15)",
    )
    p_edgar.add_argument(
        "-m",
        "--metric",
        default="Revenues",
        help="GAAP metric name for facts (default: Revenues)",
    )
    p_edgar.add_argument(
        "-f",
        "--fresh",
        action="store_true",
        help="Bypass local cache and force fresh SEC pull",
    )
    p_edgar.add_argument(
        "-o",
        "--open",
        dest="open_browser",
        action="store_true",
        help="Open the latest filing or document directly in default web browser",
    )

    # Phase 11: Maritime Tanker & Cargo Alternative Data Engine
    p_vessel = _sub(
        "vessel",
        cmd_vessel,
        "Maritime Tanker & Cargo Tracking: Crude oil, LNG, bulk, and container tracking",
        ["ais"],
    )
    p_vessel.add_argument(
        "action",
        nargs="?",
        default="list",
        choices=["list", "track", "chokepoints", "commodities"],
        help="Action to perform (default: list)",
    )
    p_vessel.add_argument(
        "identifier", nargs="?", default=None, help="Vessel IMO, MMSI, or Name to track"
    )
    p_vessel.add_argument(
        "-t",
        "--type",
        dest="vessel_type",
        default=None,
        help="Filter by vessel type (e.g. tanker, lng, bulk, container)",
    )
    p_vessel.add_argument(
        "-c",
        "--company",
        default=None,
        help="Filter by operating or chartering company (e.g. Frontline, Shell, Aramco, Maersk)",
    )
    p_vessel.add_argument(
        "-k",
        "--chokepoint",
        default=None,
        help="Filter by nearest chokepoint (e.g. hormuz, suez, malacca)",
    )
    p_vessel.add_argument(
        "-s",
        "--status",
        default=None,
        choices=["laden", "ballast", "LADEN", "BALLAST"],
        help="Filter by cargo load status (laden, ballast)",
    )
    _add_limit_arg(
        p_vessel,
        default=25,
        help_text="Maximum number of vessels to display (default: 25)",
    )

    # Quantitative Research, Trading & Risk Subparsers (Gaps 1-12)
    try:
        from ..trading_cli import add_trading_parsers

        add_trading_parsers(sub)
    except Exception:
        pass

    # Phase 12: Hierarchical Configuration Show
    p_cfg = _sub(
        "config",
        cmd_config,
        "Inspect and query hierarchical mdrap.toml configuration",
        ["cfg"],
    )
    p_cfg.add_argument(
        "config_action", nargs="?", default="show", help="Action (default: show)"
    )
    p_cfg.add_argument("--venue", help="Filter by venue code (e.g. binance, XNSE)")
    p_cfg.add_argument(
        "--instrument",
        "--symbol",
        help="Filter by instrument symbol (e.g. BTCUSDT, AAPL)",
    )
    p_cfg.add_argument(
        "--instrument-class", help="Filter by asset class (e.g. crypto, equity)"
    )

    # Phase 14: Diagnosability Doctor
    _sub(
        "doctor",
        cmd_doctor,
        "Inspect environment, compiler, engine tier, WAL status, and run 10k smoke check",
        ["doc"],
        db=True,
    )

    # Phase 14: Demo
    _sub(
        "demo",
        cmd_demo,
        "Execute bundled 50k-event run and open live desk navigator",
        ["dm"],
        db=True,
    )

    # Phase 17: Standalone Native Core (T1 Hot Path)
    p_core = _sub(
        "core",
        cmd_core,
        "Run standalone native C hot-path engine (T1 zero-lock tier)",
        ["t1", "fast-core"],
    )
    _add_events_arg(
        p_core,
        default=100000,
        help_text="Number of simulated ticks (default: 100000)",
    )
    p_core.add_argument(
        "--shm",
        default="mdrap_feed",
        help="Shared memory segment name (default: mdrap_feed)",
    )
    p_core.add_argument(
        "--rate",
        "-r",
        type=int,
        default=0,
        help="Rate throttle in events/sec (0 = unconstrained)",
    )
    p_core.add_argument(
        "--symbol", default="BTC/USD", help="Target symbol ticker (default: BTC/USD)"
    )
    p_core.add_argument(
        "--source", default="FEEDX", help="Source identifier (default: FEEDX)"
    )
    p_core.add_argument(
        "--build",
        action="store_true",
        help="Recompile mdrap-core binary before executing",
    )
    p_core.add_argument("--quiet", "-q", action="store_true", help="Suppress output")

    # Shell Autocompletion Generator
    p_comp = _sub(
        "completion",
        cmd_completion,
        "Generate shell autocompletion script (bash, zsh, fish, powershell)",
        ["complete"],
    )
    p_comp.add_argument(
        "shell",
        nargs="?",
        default="bash",
        choices=["bash", "zsh", "fish", "powershell", "pwsh"],
        help="Target shell (default: bash)",
    )

    return parser


# ---------------------------------------------------------------------------
# Wall Street Mnemonics & Fast Trading Shell Shortcuts
# ---------------------------------------------------------------------------

KNOWN_SYMBOLS = {
    "BTC": "BTC/USD",
    "BTC/USD": "BTC/USD",
    "BTCUSD": "BTC/USD",
    "ETH": "ETH/USD",
    "ETH/USD": "ETH/USD",
    "ETHUSD": "ETH/USD",
    "SOL": "SOL/USD",
    "SOL/USD": "SOL/USD",
    "SOLUSD": "SOL/USD",
    "AAPL": "AAPL",
    "MSFT": "MSFT",
    "GOOGL": "GOOGL",
    "AMZN": "AMZN",
    "NVDA": "NVDA",
    "TSLA": "TSLA",
    "META": "META",
    "JPM": "JPM",
    "ES": "ES.c.0",
    "NQ": "NQ.c.0",
    "SPY": "SPY",
    "QQQ": "QQQ",
    "IWM": "IWM",
}

MNEMONIC_MAP = {
    # Market Desk
    "bbo": "bbo",
    "nbbo": "bbo",
    "depth": "depth",
    "l2": "depth",
    "book": "depth",
    "ladder": "depth",
    "d": "depth",
    "vwap": "vwap",
    "curve": "vwap",
    "slip": "vwap",
    "slippage": "vwap",
    "v": "vwap",
    "live": "live",
    "stream": "live",
    "liv": "live",
    "watch": "live",
    "ticker": "live",
    "tick": "live",
    "focus": "live",
    "sub": "sub",
    "subscribe": "sub",
    "client": "sub",
    "listen": "sub",
    # Streaming Feeds
    "polygon": "polygon",
    "poly": "polygon",
    "p": "polygon",
    "databento": "databento",
    "dbn": "databento",
    "b": "databento",
    "feed": "feed",
    "feeds": "feed",
    "f": "feed",
    # Analytical Columnar Storage (Phase 3: DuckDB)
    "col": "columnar",
    "duck": "columnar",
    "duckdb": "columnar",
    "columnar": "columnar",
    # Quant Analytics & Technical Charting
    "chart": "chart",
    "candle": "chart",
    "candles": "chart",
    "candlestick": "chart",
    "graph": "chart",
    "plot": "chart",
    "c": "chart",
    "cnd": "ohlcv",
    "ohlcv": "ohlcv",
    "ohlc": "ohlcv",
    "gp": "chart",
    "spr": "spread",
    "spread": "spread",
    "spreads": "spread",
    "vol": "vol",
    "volatility": "vol",
    # Financial Reports & Models
    "export": "export",
    "exp": "export",
    "excel": "export",
    "xlsx": "export",
    "csv": "export",
    "x": "export",
    # Institutional Best Execution & Flow Analytics (Competitor Leapfrog)
    "tca": "tca",
    "bestex": "tca",
    "best-ex": "tca",
    "slip-audit": "tca",
    "flow": "flow",
    "cvd": "flow",
    "orderflow": "flow",
    "whales": "flow",
    "who": "flow",
    # Service & Infrastructure
    "top": "top",
    "mon": "top",
    "monitor": "top",
    "cockpit": "top",
    "daemon": "daemon",
    "dmn": "daemon",
    "serve": "serve",
    "srv": "serve",
    "api": "serve",
    "server": "serve",
    # Reliability & Audit
    "stat": "status",
    "status": "status",
    "s": "status",
    "des": "status",
    "health": "health",
    "h": "health",
    "watchdog": "watchdog",
    "wd": "watchdog",
    "w": "watchdog",
    "sec": "security",
    "security": "security",
    "keys": "keys",
    "key": "keys",
    "api-keys": "keys",
    "aud": "audit",
    "audit": "audit",
    "chaos": "chaos",
    "ch": "chaos",
    "stress": "stress",
    "str": "stress",
    "sim": "simulate",
    "simulate": "simulate",
    "usersim": "simulate",
    "devices": "simulate",
    "sim-users": "simulate",
    "test": "test-all",
    "t": "test-all",
    "test-all": "test-all",
    "bench": "benchmark",
    "benchmark": "benchmark",
    "comp": "compare",
    "compare": "compare",
    "run": "run",
    "r": "run",
    "throughput": "throughput",
    "tp": "throughput",
    "meps": "throughput",
    "million": "throughput",
    "1m": "throughput",
    "500k": "throughput",
    "historical": "historical",
    "history": "historical",
    "chd": "historical",
    "archive": "archive",
    "arc": "archive",
    "replay": "replay",
    "rep": "replay",
    "latest": "latest",
    "last": "latest",
    "lineage": "lineage",
    "lin": "lineage",
    "quarantine": "quar",
    "quar": "quar",
    "mbo": "mbo",
    "l3": "mbo",
    "queue": "mbo",
    "arbitrate": "arbitrate",
    "arb": "arbitrate",
    "multicast": "arbitrate",
    "udp": "arbitrate",
    "clear": "clear",
    "cls": "clear",
    "strategy": "strategy",
    "strat": "strategy",
    "algo": "strategy",
    "ems": "strategy",
    "help": "help",
    "menu": "help",
    "?": "help",
    "palette": "help",
    "gateway": "gateway",
    "gw": "gateway",
    "tcp-gw": "gateway",
    "sdk-demo": "sdk-demo",
    "sdk": "sdk-demo",
    "dashboard": "dashboard",
    "dash": "dashboard",
    "itch": "itch",
    "totalview": "itch",
    "edgar": "edgar",
    "research": "edgar",
    "events": "edgar",
    "filings": "edgar",
    "company": "edgar",
    "insiders": "edgar",
    "vessel": "vessel",
    "vessels": "vessel",
    "tanker": "vessel",
    "tankers": "vessel",
    "ship": "vessel",
    "ships": "vessel",
    "ais": "vessel",
    "cargo": "vessel",
    # Quantitative Research & Trading Additions (Gaps 1-12)
    "backtest": "backtest",
    "bt": "backtest",
    "risk": "risk",
    "var": "risk",
    "cvar": "risk",
    "bars": "bars",
    "bardb": "bars",
    "options": "options",
    "opt": "options",
    "greeks": "options",
    "news": "news",
    "sentiment": "news",
    "alert": "alert",
    "alerts": "alert",
    "watchlist": "watchlist",
    "wl": "watchlist",
    "portfolio": "portfolio",
    "port": "portfolio",
    "pnl": "portfolio",
    "corpact": "corpact",
    "splits": "corpact",
    "dividends": "corpact",
    "features": "features",
    "feat": "features",
    "schedule": "schedule",
    "sched": "schedule",
    "cron": "schedule",
    "retention": "retention",
    "compact": "retention",
    "prune": "retention",
    "report": "report",
    "regulatory": "report",
    "13f": "report",
    "rts28": "report",
    "markets": "markets",
    "venues": "markets",
    "world": "markets",
    "desk": "desk",
    "navigator": "desk",
    "nav": "desk",
    "tui": "desk",
    "completion": "completion",
    "complete": "completion",
    "load": "loadtest",
    "loadtest": "loadtest",
    "exit": "exit",
    "quit": "exit",
    "q": "query",
    "fo": "failover",
    "failover": "failover",
    "lake": "lake",
    "partitions": "lake",
}

QUICK_ACTIONS = {
    "0": ["desk"],
    "1": ["live", "BTC/USD"],
    "2": ["bbo", "BTC/USD"],
    "3": ["top"],
    "4": ["chart", "AAPL"],
    "5": ["depth", "BTC/USD"],
    "6": ["vwap", "AAPL"],
    "7": ["feed", "polygon"],
    "8": ["feed", "databento"],
    "9": ["status"],
}

ALL_CANONICAL_COMMANDS = [
    "failover",
    "lake",
    "historical",
    "status",
    "run",
    "benchmark",
    "compare",
    "loadtest",
    "chaos",
    "security",
    "keys",
    "serve",
    "query",
    "archive",
    "replay",
    "analytics",
    "bbo",
    "depth",
    "vwap",
    "export",
    "live",
    "chart",
    "sub",
    "ohlcv",
    "spread",
    "vol",
    "top",
    "daemon",
    "watchdog",
    "stress",
    "simulate",
    "test-all",
    "throughput",
    "archive",
    "replay",
    "latest",
    "lineage",
    "quar",
    "mbo",
    "arbitrate",
    "tca",
    "flow",
    "strategy",
    "gateway",
    "sdk-demo",
    "dashboard",
    "version",
    "itch",
    "edgar",
    "vessel",
    "backtest",
    "risk",
    "bars",
    "options",
    "news",
    "alert",
    "watchlist",
    "portfolio",
    "corpact",
    "features",
    "schedule",
    "retention",
    "report",
    "markets",
    "desk",
    "config",
    "core",
    "doctor",
    "deadletter",
    "plugins",
    "demo",
    "completion",
]


def render_command_palette(console: Console) -> None:
    """Render clean, high-density 4-quadrant Wall Street command palette."""
    render_brand_header(
        console,
        title="MDRAP PLATFORM COMMAND MATRIX",
        subtitle="Market Desk, Quant, Daemon & System Controls",
        badge=f"v{__version__}",
    )
    palette = (
        "[bold #818cf8]┌─ 🟢 Market Desk ──────────────┬─ 📊 Quant & Execution ──────────┐[/bold #818cf8]\n"
        "[bold #818cf8]│[/bold #818cf8] [bold green]BBO[/bold green]   [dim][SYM][/dim] Consolidated NBBO [bold #818cf8]│[/bold #818cf8] [bold green]TCA[/bold green]   [dim][SYM][/dim] Best-Ex SEC 606   [bold #818cf8]│[/bold #818cf8]\n"
        "[bold #818cf8]│[/bold #818cf8] [bold green]DEPTH[/bold green] [dim][SYM][/dim] L2 Order Book     [bold #818cf8]│[/bold #818cf8] [bold green]FLOW[/bold green]  [dim][SYM][/dim] Order Flow & CVD  [bold #818cf8]│[/bold #818cf8]\n"
        "[bold #818cf8]│[/bold #818cf8] [bold green]LIVE[/bold green]  [dim][SYM][/dim] In-Place Live View[bold #818cf8]│[/bold #818cf8] [bold green]CHART[/bold green] [dim][SYM][/dim] Candlestick Graph  [bold #818cf8]│[/bold #818cf8]\n"
        "[bold #818cf8]│[/bold #818cf8] [bold green]VWAP[/bold green]  [dim][SYM][/dim] Slippage Curves   [bold #818cf8]│[/bold #818cf8] [bold green]CND[/bold green]   [dim][SYM][/dim] OHLCV Table Bars  [bold #818cf8]│[/bold #818cf8]\n"
        "[bold #818cf8]│[/bold #818cf8] [bold green]SUB[/bold green]   [dim][SYM][/dim] TCP Push Stream   [bold #818cf8]│[/bold #818cf8] [bold green]EXCEL[/bold green] [dim][SYM][/dim] Financial Model   [bold #818cf8]│[/bold #818cf8]\n"
        "[bold #818cf8]│[/bold #818cf8] [bold green]GW[/bold green]         TCP Gateway Socket [bold #818cf8]│[/bold #818cf8] [bold green]SPR[/bold green]   [dim][SYM][/dim] Bid/Ask Spreads   [bold #818cf8]│[/bold #818cf8]\n"
        "[bold #818cf8]├─ ⚡ Service & Daemon ──────────┼─ 🛡️ Reliability & Security ─────┤[/bold #818cf8]\n"
        "[bold #818cf8]│[/bold #818cf8] [bold green]TOP[/bold green]        Terminal Cockpit   [bold #818cf8]│[/bold #818cf8] [bold green]STAT[/bold green]       System Overview     [bold #818cf8]│[/bold #818cf8]\n"
        "[bold #818cf8]│[/bold #818cf8] [bold green]DMN[/bold green]        Streaming Daemon   [bold #818cf8]│[/bold #818cf8] [bold green]HEALTH[/bold green]     Venue Reputation    [bold #818cf8]│[/bold #818cf8]\n"
        "[bold #818cf8]│[/bold #818cf8] [bold green]STR[/bold green]        Stress & 1B Scale  [bold #818cf8]│[/bold #818cf8] [bold green]SEC[/bold green]        HMAC & RBAC Status  [bold #818cf8]│[/bold #818cf8]\n"
        "[bold #818cf8]│[/bold #818cf8] [bold green]CHAOS[/bold green]      Failure Drills     [bold #818cf8]│[/bold #818cf8] [bold green]AUD[/bold green]        Merkle Audit Log    [bold #818cf8]│[/bold #818cf8]\n"
        "[bold #818cf8]└───────────────────────────────┴─────────────────────────────────┘[/bold #818cf8]\n"
        "[dim]⚡ 1-Key Launches: [0] Desk Navigator  [1] Live BTC  [2] BBO Quote  [3] Top Cockpit  [4] Chart  [5] Depth  [6] VWAP  [7] Polygon  [8] Databento  [9] Status[/dim]\n"
        "[dim]💡 Traders: Type '<TICKER> <CMD>' (e.g. AAPL TCA, AAPL FLOW, BTC BBO, AAPL CHART) or just ticker (e.g. AAPL)[/dim]\n"
        "[dim]⌨️ Global Flags: --no-color / --plain (suppress ANSI, honors NO_COLOR=1)  |  --json (structured data)[/dim]\n"
    )
    console.print(palette)


def cmd_shell(args=None, parser=None):
    """
    MDRAP Low-Latency Interactive Shell with Gemini/Claude-style Slash Commands & Wall Street Mnemonics.
    Pre-warms storage, C accelerator, and memory so commands execute in sub-milliseconds.
    """
    console = Console()
    if parser is None:
        parser = build_parser()

    # Enable native console tab completion where supported
    try:
        import readline

        def _completer(text, state):
            line = readline.get_line_buffer().lstrip("/")
            options = [cmd for cmd in ALL_CANONICAL_COMMANDS if cmd.startswith(line)]
            if state < len(options):
                return "/" + options[state]
            return None

        readline.set_completer(_completer)
        readline.parse_and_bind("tab: complete")
    except Exception:
        pass

    db_path = getattr(args, "db", "data/mdrap.db") if args else "data/mdrap.db"
    _ensure_db_dir(db_path)

    console.print()
    render_brand_header(
        console,
        title="MDRAP INTERACTIVE SHELL",
        subtitle="Wall Street & Quantitative Execution Terminal",
        badge="ONLINE",
        meta=f"db: {db_path}",
    )
    render_gemini_tips(console)

    while True:
        try:
            prompt = console.input(
                "[bold cyan]mdrap[/bold cyan] [bold #818cf8]❯[/bold #818cf8] "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Exiting...[/dim]")
            break

        if not prompt:
            continue

        cmd_line = prompt
        if cmd_line.startswith("/"):
            cmd_line = cmd_line[1:].strip()

        # 1. Check for Fast 1-Key Launch
        if cmd_line in QUICK_ACTIONS:
            cli_tokens = QUICK_ACTIONS[cmd_line]
            verb = cli_tokens[0]
            rest = cli_tokens[1:]
        else:
            try:
                tokens = shlex.split(cmd_line)
            except Exception as e:
                console.print(f"[red]Syntax error:[/red] {e}")
                continue

            if not tokens:
                continue

            # 2. Ticker-First Check (e.g. "BTC BBO", "AAPL CND", "BTC", "NNOX CHART")
            first_upper = tokens[0].upper()
            first_clean = first_upper.replace(".", "").replace("-", "")
            raw_token0 = tokens[0].lower()
            has_cmd_typo = bool(
                difflib.get_close_matches(
                    raw_token0,
                    list(MNEMONIC_MAP.keys()) + list(ALL_CANONICAL_COMMANDS),
                    n=1,
                    cutoff=0.6,
                )
            )
            is_ticker = (first_upper in KNOWN_SYMBOLS) or (
                not has_cmd_typo
                and raw_token0 not in MNEMONIC_MAP
                and raw_token0 not in ALL_CANONICAL_COMMANDS
                and first_clean.isalpha()
                and 1 <= len(first_clean) <= 8
            )
            if is_ticker:
                sym = KNOWN_SYMBOLS.get(first_upper, first_upper)
                if len(tokens) == 1:
                    verb = "bbo"
                    rest = [sym]
                else:
                    verb = tokens[1].lower()
                    rest = [sym] + tokens[2:]
            else:
                verb = tokens[0].lower()
                rest = tokens[1:]

            # 3. Bloomberg Mnemonic Resolution
            raw_verb = verb
            verb = MNEMONIC_MAP.get(raw_verb, raw_verb)

            # 4. Fuzzy "Did You Mean?" Autocorrect
            if verb not in MNEMONIC_MAP.values() and verb not in ALL_CANONICAL_COMMANDS:
                matches = difflib.get_close_matches(
                    raw_verb, list(MNEMONIC_MAP.keys()), n=1, cutoff=0.55
                )
                if matches:
                    suggested = MNEMONIC_MAP.get(matches[0], matches[0])
                    console.print(
                        f"[yellow]Unknown mnemonic '[bold]{raw_verb}[/bold]'. Did you mean '[bold cyan]/{suggested}[/bold cyan]'?[/yellow]"
                    )
                    try:
                        confirm = console.input(
                            f"  [dim]Press Enter to run '/{suggested}', or 'n' to cancel: [/dim]"
                        ).strip()
                    except Exception:
                        confirm = "n"
                    if confirm.lower() not in ("n", "no", "cancel"):
                        verb = suggested
                    else:
                        continue
                else:
                    console.print(
                        f"[red]Unknown command '[bold]{raw_verb}[/bold]'. Type [bold cyan]?[/bold cyan] for command palette.[/red]\n"
                    )
                    continue

            # 5. Command Palette Trigger
            if verb in ("help", "menu"):
                render_command_palette(console)
                continue

            # 6. Exit
            if verb == "exit":
                console.print("[dim]Goodbye![/dim]")
                break

            # 7. Clear Screen
            if verb == "clear":
                os.system("cls" if sys.platform == "win32" else "clear")
                continue

            # 8. Dispatch to CLI subparser
            if verb in ("live", "watch", "ticker", "tick", "focus"):
                sym = rest[0] if rest else "BTC/USD"
                cli_tokens = ["live", sym] + rest[1:]
            elif verb in ("chart", "candle", "candlestick", "graph", "plot"):
                sym = rest[0] if rest else "AAPL"
                cli_tokens = ["chart", sym] + rest[1:]
            elif verb in ("depth", "l2", "book", "ladder"):
                sym = rest[0] if rest else "BTC/USD"
                cli_tokens = ["depth", sym] + rest[1:]
            elif verb in ("vwap", "curve", "slip", "slippage"):
                sym = rest[0] if rest else "BTC/USD"
                cli_tokens = ["vwap", sym] + rest[1:]
            elif verb in ("polygon", "poly"):
                sym = rest[0] if rest else "AAPL"
                cli_tokens = ["live", sym, "--feed", "polygon", "--mock-feed"] + rest[
                    1:
                ]
            elif verb in ("databento", "dbn"):
                sym = rest[0] if rest else "ES.c.0"
                cli_tokens = ["live", sym, "--feed", "databento", "--mock-feed"] + rest[
                    1:
                ]
            elif verb in ("feed", "feeds"):
                cli_tokens = ["feed"] + rest
            elif verb == "bbo":
                sym = rest[0] if rest else "BTC/USD"
                cli_tokens = ["bbo", sym] + rest[1:]
            elif verb in ("export", "exp", "excel", "xlsx"):
                sym = rest[0] if rest else "AAPL"
                cli_tokens = ["export", sym, "--open"] + rest[1:]
            elif verb in ("tca", "bestex", "slip-audit"):
                sym = rest[0] if rest else "AAPL"
                cli_tokens = ["tca", sym] + rest[1:]
            elif verb in ("flow", "cvd", "orderflow", "whales"):
                sym = rest[0] if rest else "AAPL"
                cli_tokens = ["flow", sym] + rest[1:]
            elif verb in ("bridge", "excel-bridge", "bdp", "rtd"):
                cli_tokens = ["bridge"] + rest
            elif verb == "sub":
                sym = rest[0] if rest else "BTC/USD"
                cli_tokens = ["sub", sym] + rest[1:]
            elif verb == "ohlcv":
                sym = rest[0] if rest else "BTC/USD"
                cli_tokens = ["analytics", "ohlcv", sym] + rest[1:]
            elif verb == "spread":
                sym = rest[0] if rest else "all"
                cli_tokens = ["analytics", "spread", sym] + rest[1:]
            elif verb == "vol":
                cli_tokens = ["analytics", "vol"] + rest
            elif verb == "top":
                cli_tokens = ["top"] + rest
            elif verb in ("columnar", "col", "duck", "duckdb"):
                cli_tokens = ["columnar"] + rest
            elif verb in ("desk", "navigator", "nav", "tui"):
                cli_tokens = ["desk"] + rest
            elif verb == "daemon":
                if not rest:
                    cli_tokens = ["daemon", "--speed", "2000"]
                else:
                    cli_tokens = ["daemon"] + rest
            elif verb == "status":
                cli_tokens = ["status"] + rest
            elif verb == "health":
                cli_tokens = ["query", "health"] + rest
            elif verb == "watchdog":
                if not rest:
                    cli_tokens = ["watchdog", "status"]
                else:
                    cli_tokens = ["watchdog"] + rest
            elif verb == "security":
                cli_tokens = ["security"] + rest
            elif verb == "audit":
                cli_tokens = ["audit"] + rest
            elif verb == "chaos":
                cli_tokens = ["chaos"] + (rest if rest else ["all"])
            elif verb in ("stress", "str"):
                cli_tokens = ["stress"] + rest
            elif verb == "test-all":
                cli_tokens = ["test-all"] + rest
            elif verb == "run":
                if rest and rest[0].isdigit():
                    cli_tokens = ["run", "-e", rest[0]] + rest[1:]
                else:
                    cli_tokens = ["run"] + rest
            elif verb == "benchmark":
                if rest and rest[0].isdigit():
                    cli_tokens = ["benchmark", "-e", rest[0]] + rest[1:]
                else:
                    cli_tokens = ["benchmark"] + rest
            elif verb in ("throughput", "tp", "meps", "million"):
                if rest and rest[0].isdigit():
                    cli_tokens = ["throughput", "-e", rest[0]] + rest[1:]
                else:
                    cli_tokens = ["throughput"] + rest
            elif verb == "compare":
                if rest and rest[0].isdigit():
                    cli_tokens = ["compare", "-e", rest[0]] + rest[1:]
                else:
                    cli_tokens = ["compare"] + rest
            elif verb == "archive":
                cli_tokens = ["archive"] + rest
            elif verb == "replay":
                cli_tokens = ["replay"] + rest
            elif verb == "latest":
                sym = rest[0] if rest else "AAPL"
                cli_tokens = ["query", "latest", sym] + rest[1:]
            elif verb == "lineage":
                cli_tokens = ["query", "lineage"] + rest
            elif verb == "quar":
                cli_tokens = ["query", "quarantine"] + rest
            elif verb in (
                "edgar",
                "research",
                "events",
                "filings",
                "company",
                "insiders",
                "facts",
                "profile",
            ):
                EDGAR_ACTIONS = ("events", "insiders", "profile", "facts", "filings")
                if verb in ("events", "insiders", "filings", "facts"):
                    cli_tokens = ["edgar", verb] + rest
                elif verb in ("company", "profile"):
                    cli_tokens = ["edgar", "profile"] + rest
                else:
                    if rest:
                        action_cand = rest[0].lower()
                        if action_cand in EDGAR_ACTIONS:
                            cli_tokens = ["edgar", action_cand] + rest[1:]
                        else:
                            close = difflib.get_close_matches(
                                action_cand, EDGAR_ACTIONS, n=1, cutoff=0.6
                            )
                            if close:
                                console.print(
                                    f"[dim cyan][auto-correct] Interpreting '{action_cand}' as '{close[0]}'[/dim cyan]"
                                )
                                cli_tokens = ["edgar", close[0]] + rest[1:]
                            elif (
                                len(rest) > 1
                                and not rest[0].startswith("-")
                                and not rest[1].startswith("-")
                            ):
                                cli_tokens = ["edgar", rest[0]] + rest[1:]
                            elif not rest[0].startswith("-"):
                                cli_tokens = ["edgar", "events"] + rest
                            else:
                                cli_tokens = ["edgar"] + rest
                    else:
                        cli_tokens = ["edgar"]

            elif verb in (
                "vessel",
                "vessels",
                "tanker",
                "tankers",
                "ship",
                "ships",
                "ais",
                "cargo",
            ):
                if (
                    rest
                    and rest[0] not in ("list", "track", "chokepoints", "commodities")
                    and not rest[0].startswith("-")
                ):
                    cli_tokens = ["vessel", "track"] + rest
                else:
                    cli_tokens = ["vessel"] + rest

            elif verb in ("news", "sentiment"):
                NEWS_ACTIONS = ("latest", "analyze", "summary", "fetch")
                if rest:
                    action_cand = rest[0].lower()
                    if action_cand in NEWS_ACTIONS:
                        cli_tokens = ["news", action_cand] + rest[1:]
                    else:
                        close = difflib.get_close_matches(
                            action_cand, NEWS_ACTIONS, n=1, cutoff=0.6
                        )
                        if close:
                            console.print(
                                f"[dim cyan][auto-correct] Interpreting '{action_cand}' as '{close[0]}'[/dim cyan]"
                            )
                            cli_tokens = ["news", close[0]] + rest[1:]
                        elif not rest[0].startswith("-"):
                            cli_tokens = ["news", "latest", "-s", rest[0]] + rest[1:]
                        else:
                            cli_tokens = ["news", "latest"] + rest
                else:
                    cli_tokens = ["news", "latest"]
            else:
                if verb not in MNEMONIC_MAP and verb not in ALL_CANONICAL_COMMANDS:
                    close = difflib.get_close_matches(
                        verb, list(MNEMONIC_MAP.keys()), n=1, cutoff=0.55
                    )
                    if close:
                        console.print(
                            f"[bold red]Unknown command:[/bold red] '{verb}'. Did you mean [bold green]{close[0]}[/bold green]?\n"
                        )
                    else:
                        console.print(
                            f"[bold red]Unknown command:[/bold red] '{verb}'. Type [green]help[/green] or [green]status[/green] for available commands.\n"
                        )
                    continue
                cli_tokens = [verb] + rest

        # Execute with sub-millisecond timer
        t0 = time.perf_counter()
        try:
            parsed_args = parser.parse_args(cli_tokens)
            parsed_args.func(parsed_args)
            t1 = time.perf_counter()
            elapsed_ms = (t1 - t0) * 1000.0
            if verb in ("live", "stream"):
                events_n = getattr(parsed_args, "limit", 20) or 20
                per_tick_ms = elapsed_ms / max(events_n, 1)
                eps = (events_n / (elapsed_ms / 1000.0)) if elapsed_ms > 0 else 0.0
                if elapsed_ms < 1000.0:
                    console.print(
                        f"[dim green]Live stream completed in {elapsed_ms:.1f} ms ({per_tick_ms:.1f} ms/tick, {eps:.1f} eps)[/dim green]\n"
                    )
                else:
                    console.print(
                        f"[dim green]Live stream completed in {elapsed_ms / 1000.0:.2f}s ({per_tick_ms:.1f} ms/tick, {eps:.1f} eps)[/dim green]\n"
                    )
            elif verb in (
                "compare",
                "comp",
                "bench",
                "benchmark",
                "run",
                "r",
                "test",
                "test-all",
                "t",
                "chaos",
                "ch",
            ):
                console.print(
                    f"[dim green]Batch command finished in {elapsed_ms / 1000.0:.2f}s (total multi-run elapsed time)[/dim green]\n"
                )
            else:
                console.print(
                    f"[dim green]Query executed in {elapsed_ms:.2f} ms[/dim green]\n"
                )
        except SystemExit:
            pass
        except Exception as exc:
            console.print(f"[bold red]Command error:[/bold red] {exc}\n")


def main():
    # Detect explicit color suppression before any Console is constructed
    if "--no-color" in sys.argv or "--plain" in sys.argv or os.environ.get("NO_COLOR"):
        os.environ["NO_COLOR"] = "1"
        os.environ["MDRAP_NO_COLOR"] = "1"

    # Pre-process direct slash commands, Wall Street mnemonics, or ticker-first syntax
    if len(sys.argv) > 1:
        arg1 = sys.argv[1]
        raw_cmd = arg1.lstrip("/").lower() if arg1.startswith("/") else arg1.lower()
        orig_cmd = raw_cmd

        # Check for 1-key launch shortcuts
        if raw_cmd in QUICK_ACTIONS:
            sys.argv = [sys.argv[0]] + QUICK_ACTIONS[raw_cmd]
            raw_cmd = sys.argv[1]

        # Check for Help / Command Palette request
        if raw_cmd in ("?", "help", "menu", "palette", "-h", "--help", "-help"):
            if len(sys.argv) > 2 and raw_cmd in ("help", "?"):
                topic = sys.argv[2].lower().lstrip("/")
                topic = MNEMONIC_MAP.get(topic, topic)
                sys.argv = [sys.argv[0], topic, "--help"]
                raw_cmd = topic
            else:
                render_command_palette(Console())
                return

        # Check for Ticker-First syntax (e.g. `mdrap btc bbo`, `mdrap aapl cnd`, `mdrap btc`)
        first_upper = raw_cmd.upper()
        first_clean = first_upper.replace(".", "").replace("-", "")
        has_cmd_typo = bool(
            difflib.get_close_matches(
                raw_cmd,
                list(MNEMONIC_MAP.keys()) + list(ALL_CANONICAL_COMMANDS),
                n=1,
                cutoff=0.6,
            )
        )
        is_ticker_first = not raw_cmd.startswith("-") and (
            (first_upper in KNOWN_SYMBOLS)
            or (
                not has_cmd_typo
                and raw_cmd not in MNEMONIC_MAP
                and raw_cmd not in ALL_CANONICAL_COMMANDS
                and first_clean.isalpha()
                and 1 <= len(first_clean) <= 8
            )
        )
        if is_ticker_first:
            sym = KNOWN_SYMBOLS.get(first_upper, first_upper)
            if len(sys.argv) == 2:
                sys.argv = [sys.argv[0], "bbo", sym]
                raw_cmd = "bbo"
            else:
                func = sys.argv[2].lower().lstrip("/")
                func = MNEMONIC_MAP.get(func, func)
                sys.argv = [sys.argv[0], func, sym] + sys.argv[3:]
                raw_cmd = func

        # Expand mnemonics
        if raw_cmd in MNEMONIC_MAP:
            raw_cmd = MNEMONIC_MAP[raw_cmd]
            sys.argv[1] = raw_cmd
        elif raw_cmd in ALL_CANONICAL_COMMANDS or raw_cmd.startswith("-"):
            pass
        else:
            # Fuzzy match typo correction for CLI command line
            matches = difflib.get_close_matches(
                raw_cmd,
                list(MNEMONIC_MAP.keys()) + list(ALL_CANONICAL_COMMANDS),
                n=1,
                cutoff=0.55,
            )
            if matches:
                suggested = MNEMONIC_MAP.get(matches[0], matches[0])
                if suggested != raw_cmd:
                    print(
                        f"[mdrap] Notice: Auto-correcting '{orig_cmd}' -> '{suggested}'",
                        file=sys.stderr,
                    )
                    raw_cmd = suggested
                    sys.argv[1] = suggested

        if raw_cmd in ("desk", "navigator", "nav", "tui"):
            sys.argv = [sys.argv[0], "desk"] + sys.argv[2:]
        elif raw_cmd in ("live", "stream"):
            sym = sys.argv[2] if len(sys.argv) > 2 else "BTC/USD"
            sym = KNOWN_SYMBOLS.get(sym.upper(), sym)
            sys.argv = [sys.argv[0], "live", sym] + sys.argv[3:]
        elif raw_cmd in ("polygon", "poly"):
            sym = (
                sys.argv[2]
                if len(sys.argv) > 2 and not sys.argv[2].startswith("-")
                else "AAPL"
            )
            sym = KNOWN_SYMBOLS.get(sym.upper(), sym)
            rest = (
                sys.argv[3:]
                if len(sys.argv) > 2 and not sys.argv[2].startswith("-")
                else sys.argv[2:]
            )
            sys.argv = [
                sys.argv[0],
                "live",
                sym,
                "--feed",
                "polygon",
                "--mock-feed",
            ] + rest
        elif raw_cmd in ("databento", "dbn"):
            if len(sys.argv) > 2 and sys.argv[2].isdigit():
                sys.argv = [sys.argv[0], "benchmark", "-e", sys.argv[2]] + sys.argv[3:]
            else:
                sym = (
                    sys.argv[2]
                    if len(sys.argv) > 2 and not sys.argv[2].startswith("-")
                    else "ES.c.0"
                )
                sym = KNOWN_SYMBOLS.get(sym.upper(), sym)
                rest = (
                    sys.argv[3:]
                    if len(sys.argv) > 2 and not sys.argv[2].startswith("-")
                    else sys.argv[2:]
                )
                sys.argv = [
                    sys.argv[0],
                    "live",
                    sym,
                    "--feed",
                    "databento",
                    "--mock-feed",
                ] + rest
        elif raw_cmd in ("feed", "feeds"):
            sys.argv = [sys.argv[0], "feed"] + sys.argv[2:]
        elif raw_cmd in ("depth", "l2", "book", "ladder"):
            sym = sys.argv[2] if len(sys.argv) > 2 else "BTC/USD"
            sym = KNOWN_SYMBOLS.get(sym.upper(), sym)
            sys.argv = [sys.argv[0], "depth", sym] + sys.argv[3:]
        elif raw_cmd in ("vwap", "curve", "slip", "slippage"):
            sym = sys.argv[2] if len(sys.argv) > 2 else "BTC/USD"
            sym = KNOWN_SYMBOLS.get(sym.upper(), sym)
            sys.argv = [sys.argv[0], "vwap", sym] + sys.argv[3:]
        elif raw_cmd in ("export", "exp", "excel", "xlsx"):
            sym = (
                sys.argv[2]
                if len(sys.argv) > 2 and not sys.argv[2].startswith("-")
                else "AAPL"
            )
            sym = KNOWN_SYMBOLS.get(sym.upper(), sym)
            rest = (
                sys.argv[3:]
                if len(sys.argv) > 2 and not sys.argv[2].startswith("-")
                else sys.argv[2:]
            )
            sys.argv = [sys.argv[0], "export", sym] + rest
        elif raw_cmd in ("tca", "bestex", "slip-audit"):
            sym = (
                sys.argv[2]
                if len(sys.argv) > 2 and not sys.argv[2].startswith("-")
                else "AAPL"
            )
            sym = KNOWN_SYMBOLS.get(sym.upper(), sym)
            rest = (
                sys.argv[3:]
                if len(sys.argv) > 2 and not sys.argv[2].startswith("-")
                else sys.argv[2:]
            )
            sys.argv = [sys.argv[0], "tca", sym] + rest
        elif raw_cmd in ("flow", "cvd", "orderflow", "whales"):
            sym = (
                sys.argv[2]
                if len(sys.argv) > 2 and not sys.argv[2].startswith("-")
                else "AAPL"
            )
            sym = KNOWN_SYMBOLS.get(sym.upper(), sym)
            rest = (
                sys.argv[3:]
                if len(sys.argv) > 2 and not sys.argv[2].startswith("-")
                else sys.argv[2:]
            )
            sys.argv = [sys.argv[0], "flow", sym] + rest
        elif raw_cmd in ("bbo", "nbbo"):
            sym = sys.argv[2] if len(sys.argv) > 2 else "BTC/USD"
            sym = KNOWN_SYMBOLS.get(sym.upper(), sym)
            sys.argv = [sys.argv[0], "bbo", sym] + sys.argv[3:]
        elif raw_cmd in ("chart", "candle", "candles"):
            if len(sys.argv) > 2 and sys.argv[2].isdigit():
                sys.argv = [sys.argv[0], "compare", "-e", sys.argv[2]] + sys.argv[3:]
            else:
                sym = sys.argv[2] if len(sys.argv) > 2 else "AAPL"
                sym = KNOWN_SYMBOLS.get(sym.upper(), sym)
                sys.argv = [sys.argv[0], "chart", sym] + sys.argv[3:]
        elif raw_cmd in ("spread", "spreads"):
            sym = sys.argv[2] if len(sys.argv) > 2 else "all"
            sys.argv = [sys.argv[0], "analytics", "spread", sym] + sys.argv[3:]
        elif raw_cmd in ("vol", "volatility", "v"):
            sys.argv = [sys.argv[0], "analytics", "vol"] + sys.argv[2:]
        elif raw_cmd in ("health", "h"):
            sys.argv = [sys.argv[0], "query", "health"] + sys.argv[2:]
        elif raw_cmd in ("latest", "last"):
            sym = sys.argv[2] if len(sys.argv) > 2 else "AAPL"
            sys.argv = [sys.argv[0], "query", "latest", sym] + sys.argv[3:]
        elif raw_cmd in ("lineage", "lin"):
            sys.argv = [sys.argv[0], "query", "lineage"] + sys.argv[2:]
        elif raw_cmd in ("status", "s", "stat"):
            sys.argv = [sys.argv[0], "status"] + sys.argv[2:]
        elif raw_cmd in ("watchdog", "w", "wd"):
            sys.argv = [sys.argv[0], "watchdog"] + sys.argv[2:]
        elif raw_cmd in ("test", "t", "test-all"):
            sys.argv = [sys.argv[0], "test-all"] + sys.argv[2:]
        elif raw_cmd in ("run", "r"):
            if len(sys.argv) > 2 and sys.argv[2].isdigit():
                sys.argv = [sys.argv[0], "run", "-e", sys.argv[2]] + sys.argv[3:]
            else:
                sys.argv = [sys.argv[0], "run"] + sys.argv[2:]
        elif raw_cmd in ("bench", "b", "benchmark"):
            if len(sys.argv) > 2 and sys.argv[2].isdigit():
                sys.argv = [sys.argv[0], "benchmark", "-e", sys.argv[2]] + sys.argv[3:]
            else:
                sys.argv = [sys.argv[0], "benchmark"] + sys.argv[2:]
        elif raw_cmd in ("throughput", "tp", "meps", "million", "1m", "500k"):
            default_events = "500000" if orig_cmd == "500k" else "1000000"
            if len(sys.argv) > 2 and sys.argv[2].isdigit():
                sys.argv = [sys.argv[0], "throughput", "-e", sys.argv[2]] + sys.argv[3:]
            elif len(sys.argv) > 2:
                sys.argv = [sys.argv[0], "throughput"] + sys.argv[2:]
            else:
                sys.argv = [sys.argv[0], "throughput", "-e", default_events]
        elif raw_cmd in ("compare", "c", "comp"):
            if len(sys.argv) > 2 and sys.argv[2].isdigit():
                sys.argv = [sys.argv[0], "compare", "-e", sys.argv[2]] + sys.argv[3:]
            else:
                sys.argv = [sys.argv[0], "compare"] + sys.argv[2:]
        elif raw_cmd in ("security", "sec"):
            sys.argv = [sys.argv[0], "security"] + sys.argv[2:]
        elif raw_cmd in ("audit", "aud"):
            sys.argv = [sys.argv[0], "audit"] + sys.argv[2:]
        elif raw_cmd in ("chaos", "ch"):
            sys.argv = [sys.argv[0], "chaos"] + sys.argv[2:]
        elif raw_cmd in ("stress", "str"):
            sys.argv = [sys.argv[0], "stress"] + sys.argv[2:]
        elif raw_cmd in ("daemon", "d"):
            sys.argv = [sys.argv[0], "daemon"] + sys.argv[2:]
        elif raw_cmd in ("sub", "subscribe"):
            sym = sys.argv[2] if len(sys.argv) > 2 else "ALL"
            sys.argv = [sys.argv[0], "sub", sym] + sys.argv[3:]
        elif raw_cmd in ("top", "mon", "monitor"):
            sys.argv = [sys.argv[0], "top"] + sys.argv[2:]
        elif raw_cmd in ("columnar", "col", "duck", "duckdb"):
            sys.argv = [sys.argv[0], "columnar"] + sys.argv[2:]
        elif raw_cmd in ("simulate", "usersim", "devices", "sim-users", "sim"):
            sys.argv = [sys.argv[0], "simulate"] + sys.argv[2:]
        elif raw_cmd in ("itch", "totalview"):
            sys.argv = [sys.argv[0], "itch"] + sys.argv[2:]
        elif raw_cmd in (
            "edgar",
            "research",
            "events",
            "filings",
            "company",
            "insiders",
            "facts",
            "profile",
        ):
            EDGAR_ACTIONS = ("events", "insiders", "profile", "facts", "filings")
            if orig_cmd in ("events", "insiders", "filings", "facts"):
                sys.argv = [sys.argv[0], "edgar", orig_cmd] + sys.argv[2:]
            elif orig_cmd in ("company", "profile"):
                sys.argv = [sys.argv[0], "edgar", "profile"] + sys.argv[2:]
            else:
                rest = sys.argv[2:]
                if rest:
                    action_cand = rest[0].lower()
                    if action_cand in EDGAR_ACTIONS:
                        sys.argv = [sys.argv[0], "edgar", action_cand] + rest[1:]
                    else:
                        close = difflib.get_close_matches(
                            action_cand, EDGAR_ACTIONS, n=1, cutoff=0.6
                        )
                        if close:
                            print(
                                f"[mdrap] Notice: Auto-correcting '{action_cand}' -> '{close[0]}'",
                                file=sys.stderr,
                            )
                            sys.argv = [sys.argv[0], "edgar", close[0]] + rest[1:]
                        elif (
                            len(rest) > 1
                            and not rest[0].startswith("-")
                            and not rest[1].startswith("-")
                        ):
                            sys.argv = [sys.argv[0], "edgar", rest[0]] + rest[1:]
                        elif not rest[0].startswith("-"):
                            sys.argv = [sys.argv[0], "edgar", "events"] + rest
                        else:
                            sys.argv = [sys.argv[0], "edgar"] + rest
                else:
                    sys.argv = [sys.argv[0], "edgar"]

        elif raw_cmd in (
            "vessel",
            "vessels",
            "tanker",
            "tankers",
            "ship",
            "ships",
            "ais",
            "cargo",
        ):
            if (
                len(sys.argv) > 2
                and sys.argv[2] not in ("list", "track", "chokepoints", "commodities")
                and not sys.argv[2].startswith("-")
            ):
                sys.argv = [sys.argv[0], "vessel", "track"] + sys.argv[2:]
            else:
                sys.argv = [sys.argv[0], "vessel"] + sys.argv[2:]

        elif raw_cmd in ("news", "sentiment"):
            NEWS_ACTIONS = ("latest", "analyze", "summary", "fetch")
            rest = sys.argv[2:]
            if rest:
                action_cand = rest[0].lower()
                if action_cand in NEWS_ACTIONS:
                    sys.argv = [sys.argv[0], "news", action_cand] + rest[1:]
                else:
                    close = difflib.get_close_matches(
                        action_cand, NEWS_ACTIONS, n=1, cutoff=0.6
                    )
                    if close:
                        print(
                            f"[mdrap] Notice: Auto-correcting '{action_cand}' -> '{close[0]}'",
                            file=sys.stderr,
                        )
                        sys.argv = [sys.argv[0], "news", close[0]] + rest[1:]
                    elif not rest[0].startswith("-"):
                        sys.argv = [
                            sys.argv[0],
                            "news",
                            "latest",
                            "-s",
                            rest[0],
                        ] + rest[1:]
                    else:
                        sys.argv = [sys.argv[0], "news", "latest"] + rest
            else:
                sys.argv = [sys.argv[0], "news", "latest"]
        elif arg1.startswith("/"):
            sys.argv[1] = raw_cmd

    parser = build_parser()

    # If no arguments provided, launch the warm interactive slash-command shell
    if len(sys.argv) == 1:
        cmd_shell(None, parser)
        return

    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()
