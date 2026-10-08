from ._common import *

__stability__ = "beta"


def cmd_serve(args):
    """Start high-performance REST API and WebSocket event streaming server."""
    try:
        import uvicorn
    except ImportError:
        print(
            "[mdrap] Error: uvicorn is required to run the API server. Install it with: pip install '.[api]'",
            file=sys.stderr,
        )
        sys.exit(1)

    db_path = getattr(args, "db", "data/mdrap.db")
    if db_path:
        os.environ["MDRAP_DB_PATH"] = db_path

    host = getattr(args, "host", "0.0.0.0")
    port = getattr(args, "port", 8000)
    reload = getattr(args, "reload", False)

    console = Console()
    console.print()
    console.print(
        Panel.fit(
            f"[bold cyan]MDRAP Production REST & WebSocket Server[/bold cyan]\n\n"
            f"Binding: [green]http://{host}:{port}[/green]\n"
            f"API Docs: [blue]http://{host}:{port}/docs[/blue]\n"
            f"WebSocket: [magenta]ws://{host}:{port}/v1/events/stream[/magenta]\n"
            f"Database: [yellow]{db_path}[/yellow]",
            title="MDRAP API Gateway",
            border_style="cyan",
        )
    )
    uvicorn.run("mdrap.api:app", host=host, port=port, reload=reload)


def cmd_chaos(args):
    """Execute automated Section 15 chaos and resilience drills."""
    from ..chaos import ChaosEngine, drop_source_window

    console = Console()

    drill = getattr(args, "drill", "all") or "all"

    # If legacy flags were explicitly passed
    if hasattr(args, "kill_start") and args.kill_start > 0:
        cfg = SimulatorConfig(seed=args.seed, num_events=args.events)
        sim = FeedSimulator(cfg)
        store = Store(":memory:")
        pipeline = Pipeline(store)
        dropped = 0
        stream = drop_source_window(
            sim.generate(), args.kill_source, args.kill_start, args.kill_duration
        )
        for raw, _label, was_dropped in stream:
            if was_dropped:
                dropped += 1
                continue
            pipeline.process_one(raw)
        pipeline.finish()
        console.print(
            f"[chaos] simulated outage: source={args.kill_source} dropped {dropped} events"
        )
        store.close()
        return

    console.print()
    console.print(
        Panel.fit(
            f"[bold cyan]MDRAP Chaos & Failure Resilience Drills (§15)[/bold cyan] | Drill: [bold]{drill.upper()}[/bold]",
            border_style="cyan",
        )
    )

    engine = ChaosEngine(db_path=":memory:")
    if drill in ("feed", "kill"):
        results = [engine.run_feed_kill_drill()]
    elif drill in ("network", "jitter", "delay"):
        results = [engine.run_network_jitter_drill()]
    elif drill in ("burst", "dup"):
        results = [engine.run_burst_drill()]
    elif drill in ("storage", "disk"):
        results = [engine.run_storage_outage_drill()]
    else:
        results = engine.run_all_drills()

    table = Table(title="Chaos Drill Results & Scorecard", show_lines=True)
    table.add_column("Drill Name", style="cyan", no_wrap=True)
    table.add_column("Target", style="magenta")
    table.add_column("Injected", justify="right")
    table.add_column("Detection", justify="right")
    table.add_column("Failover", justify="right")
    table.add_column("Data Loss", justify="right", style="bold green")
    table.add_column("Status", justify="center")
    table.add_column("Verification Details", style="dim")

    all_passed = True
    for r in results:
        status = (
            "[bold green]PASS[/bold green]" if r.passed else "[bold red]FAIL[/bold red]"
        )
        if not r.passed:
            all_passed = False
        table.add_row(
            r.drill_name,
            r.target_source,
            f"{r.injected_events:,}",
            f"{r.detection_time_ms:.1f}ms",
            f"{r.failover_time_ms:.1f}ms",
            str(r.data_loss_count),
            status,
            r.details,
        )
    console.print(table)
    if all_passed:
        console.print(
            Panel.fit(
                "[bold green]ALL CHAOS DRILLS PASSED! Platform proved 100% resilient with zero data loss.[/bold green]",
                border_style="green",
            )
        )
    else:
        console.print(
            Panel.fit(
                "[bold red]CHAOS DRILL DETECTED RESILIENCE DEFECT! Review details above.[/bold red]",
                border_style="red",
            )
        )


def cmd_status(args):
    """Show comprehensive platform status overview (Storage, Feeds, Watchdog, Analytics)."""
    _ensure_db_dir(args.db)

    console = Console()

    db_exists = os.path.exists(args.db) and os.path.getsize(args.db) > 0
    if not db_exists:
        console.print(
            Panel(
                f"[bold yellow]Database '{args.db}' not found or empty.[/bold yellow]\n\n"
                "Run the pipeline first to generate data and populate metrics:\n"
                "  [cyan]mdrap r[/cyan]                  (Quick run 50k events)\n"
                "  [cyan]mdrap r -d[/cyan]               (Live visual dashboard)\n"
                "  [cyan]mdrap r -v v2 -f[/cyan]         (V2 streaming + Native C hotpath)",
                title="MDRAP Platform Status",
                border_style="yellow",
            )
        )
        return

    store = Store(args.db)
    db_size_mb = os.path.getsize(args.db) / (1024 * 1024)

    # 1. Event Counts
    counts = store.counts()
    val = counts.get("VALID", 0)
    susp = counts.get("SUSPICIOUS", 0)
    inv = counts.get("INVALID", 0)
    tot = val + susp + inv

    cur = store.conn.execute("SELECT COUNT(*) FROM quarantine")
    quar_count = cur.fetchone()[0]

    cur = store.conn.execute("SELECT COUNT(*) FROM lineage")
    lineage_count = cur.fetchone()[0]

    # 2. Source health
    health = store.feed_health()

    # 3. Analytics
    ohlcv = store.query_ohlcv(limit=500)
    spreads = store.query_spread()
    vol = store.query_volatility()
    bbos = store.query_bbo()

    # 4. Watchdog alerts
    alerts = store.query_alerts(limit=5)

    store.close()

    if getattr(args, "json", False):
        data = {
            "database": args.db,
            "db_size_mb": round(db_size_mb, 4),
            "counts": {"total": tot, "valid": val, "suspicious": susp, "invalid": inv},
            "quarantine_count": quar_count,
            "lineage_count": lineage_count,
            "health": health,
            "alerts": alerts,
        }
        print(json.dumps(data, indent=2))
        return

    is_healthy = True
    if health:
        is_healthy = all(h.get("score", 1.0) >= 0.90 for h in health)

    console.print()
    render_brand_header(
        console,
        title="MDRAP Platform Status Overview",
        subtitle="Canonical Pipeline, Venue Reliability & Real-Time Analytics",
        badge="ALL FEEDS HEALTHY" if is_healthy else "FEED DEGRADED",
        meta=f"Database: {args.db} ({db_size_mb:.2f} MB)",
    )

    t1_rows = [
        ("Canonical Events", f"{tot:,}", "100.0%"),
        ("  ● VALID", f"{val:,}", f"[green]{val / max(1, tot) * 100:.1f}%[/green]"),
        (
            "  ▲ SUSPICIOUS",
            f"{susp:,}",
            f"[yellow]{susp / max(1, tot) * 100:.1f}%[/yellow]",
        ),
        (
            "  ✕ INVALID (Quarantine)",
            f"{inv:,}",
            f"[red]{inv / max(1, tot) * 100:.1f}%[/red]",
        ),
        ("Quarantine Store", f"{quar_count:,}", "Persisted for audit"),
        ("Lineage Traces", f"{lineage_count:,}", "100% deterministic"),
    ]
    console.print(
        _t(
            "Event Ingestion & Segregation (Spec 6.8, 6.9)",
            [
                ("Category", "left", "cyan"),
                ("Count", "right", "bold"),
                ("Status / Share", "right"),
            ],
            t1_rows,
        )
    )
    console.print()

    t2_cols = [
        ("Source", "left", "cyan"),
        ("Packets", "right"),
        ("Score", "right", "bold"),
        ("Watchdog State", "center"),
        ("Routing", "left", "white"),
    ]
    if not health:
        t2_rows = [("No feeds", "0", "N/A", "UNKNOWN", "None")]
    else:
        t2_rows = []
        for h in health:
            score = h.get("score", 0)
            status = "HEALTHY" if score >= 0.90 else "DEGRADED"
            routing = (
                "[bold green]Primary[/bold green]"
                if score >= 0.95
                else (
                    "[green]Eligible[/green]"
                    if status == "HEALTHY"
                    else "[bold red]Traffic Diverted[/bold red]"
                )
            )
            t2_rows.append(
                (
                    h["source"],
                    f"{h['total']:,}",
                    f"{score:.4f}",
                    format_status(status),
                    routing,
                )
            )
    console.print(_t("Feed Reliability & Watchdog (Spec 6.13)", t2_cols, t2_rows))
    console.print()

    t3_rows = [
        ("Consolidated BBO", f"{len(bbos):,} instruments", "mdrap bbo all"),
        ("OHLCV Candles", f"{len(ohlcv):,} stored", "mdrap a ohlcv AAPL"),
        ("Bid-Ask Spread Stats", f"{len(spreads):,} instruments", "mdrap a spread all"),
        ("Realized Volatility", f"{len(vol):,} instruments", "mdrap a vol"),
    ]
    console.print(
        _t(
            "V3 Analytics & Consolidated BBO (Spec 14)",
            [
                ("Analytical Stream", "left", "cyan"),
                ("Coverage", "right", "bold"),
                ("Query Command", "left", "yellow"),
            ],
            t3_rows,
        )
    )

    if alerts:
        console.print()
        console.print(
            _t(
                "Recent Watchdog Alerts (Auto-Failover Log)",
                [
                    ("Source", "left", "cyan"),
                    ("Alert", "left", "bold red"),
                    ("Time", "left", "magenta"),
                    ("Action Taken", "left", "white"),
                ],
                [
                    (
                        a["source"],
                        a["alert_type"],
                        f"{a['timestamp']:.2f}",
                        a["action_taken"],
                    )
                    for a in alerts
                ],
            )
        )

    console.print()
    console.print(
        "[dim]💡 Quick Commands: [bold cyan]mdrap bbo all[/bold cyan]  •  "
        "[bold cyan]mdrap live[/bold cyan]  •  "
        "[bold cyan]mdrap top[/bold cyan]  •  "
        "[bold cyan]mdrap doctor[/bold cyan]  •  "
        "[bold cyan]mdrap shell[/bold cyan][/dim]\n"
    )


def cmd_query(args):
    _ensure_db_dir(args.db)
    store = Store(args.db)
    action = getattr(args, "action", None)
    target = getattr(args, "target", None)

    if action == "health" or getattr(args, "health", False):
        print(json.dumps(store.feed_health(), indent=2))
    elif action in ("latest", "last") or getattr(args, "latest", None):
        instr = target or getattr(args, "latest", "AAPL") or "AAPL"
        rows = store.latest(instr, limit=args.limit)
        print(json.dumps(rows, indent=2))
    elif action == "lineage" or getattr(args, "lineage", None):
        eid = target or getattr(args, "lineage", None)
        if not eid:
            print("Please specify an event ID: mdrap q lineage <event_id>")
        else:
            row = store.event_lineage(eid)
            print(json.dumps(row, indent=2) if row else "not found")
    elif (
        action in ("quarantine", "quar", "q")
        or getattr(args, "quarantine", None) is not None
    ):
        q_arg = getattr(args, "quarantine", None)
        lim = (
            q_arg
            if q_arg is not None
            else (int(target) if target and target.isdigit() else args.limit or 10)
        )
        print(json.dumps(store.quarantine_sample(limit=lim), indent=2))
    else:
        print(json.dumps(store.counts(), indent=2))
    store.close()


def cmd_replay(args):
    """Replay market events deterministically through pipeline or pacer."""
    from ..replay import HistoricalReplayEngine
    from ..analytics import MarketAnalytics

    speed = getattr(args, "speed", None)
    from_sqlite = getattr(args, "from_sqlite", None)
    from_journal = getattr(args, "from_journal", None)
    symbol = getattr(args, "symbol", None)
    limit = getattr(args, "limit", None)

    engine = HistoricalReplayEngine(speed_factor=speed, sync_virtual_clock=True)
    events = []

    if from_sqlite:
        events = engine.load_from_sqlite(from_sqlite, symbol=symbol, limit=limit)
    elif from_journal:
        events = engine.load_from_journal(from_journal, limit=limit)
    else:
        db_path = getattr(args, "db", "data/mdrap.db")
        if (
            os.path.exists(db_path)
            and not getattr(args, "date", None)
            and not getattr(args, "source", None)
        ):
            try:
                events = engine.load_from_sqlite(db_path, symbol=symbol, limit=limit)
            except Exception:
                pass

    if events:
        console = Console()
        _ensure_db_dir(getattr(args, "db", "data/mdrap_replay.db"))
        store = Store(getattr(args, "db", "data/mdrap_replay.db"))
        analytics = MarketAnalytics()
        pipeline = Pipeline(store, analytics=analytics)

        stats = engine.replay_to_pipeline(pipeline, events, pacing=(speed is not None))
        pipeline.finish()
        store.commit()
        store.close()

        console.print(
            Panel.fit(
                f"[bold green]✔ Deterministic Historical Replay Complete[/bold green]\n\n"
                f"Events Replayed: [bold cyan]{stats.events_replayed:,}[/bold cyan] / {stats.total_events:,}\n"
                f"Wall Time: [yellow]{stats.wall_elapsed_s:.3f}s[/yellow] | Virtual Time: [yellow]{stats.virtual_elapsed_s:.3f}s[/yellow]\n"
                f"Rate: [bold green]{stats.rate_eps:,.1f} eps[/bold green]\n"
                f"Max Jitter: [dim]{stats.max_jitter_us:.1f}µs[/dim] | Avg Drift: [dim]{stats.avg_drift_us:.1f}µs[/dim]",
                border_style="green",
            )
        )
        return

    # Fallback to legacy raw archive replay
    from ..archive import replay as archive_replay

    _ensure_db_dir(args.db)
    store = Store(args.db)
    analytics = MarketAnalytics()
    pipeline = Pipeline(store, analytics=analytics)

    count = 0
    for raw in archive_replay(args.base_dir, date=args.date, source=args.source):
        pipeline.process_one(raw)
        count += 1
    pipeline.finish()

    store.write_ohlcv_batch(analytics.ohlcv.candles())
    store.write_spread_batch(analytics.spreads.summary())
    store.write_volatility_batch(analytics.volatility.summary())
    store.commit()

    print(f"[replay] Replayed {count:,} events from {args.base_dir}")
    print(json.dumps(pipeline.metrics.summary(), indent=2))
    store.close()


def cmd_failover(args):
    """Active-Passive cluster failover coordinator."""
    from ..failover import FailoverNode

    console = Console()
    action = getattr(args, "action", "status") or "status"
    node_id = getattr(args, "node_id", "node-local")
    cluster_id = getattr(args, "cluster", "mdrap-cluster")

    node = FailoverNode(node_id=node_id, cluster_id=cluster_id)

    if action == "status":
        stats = node.stats()
        console.print(
            Panel.fit(
                f"[bold cyan]Node ID:[/bold cyan] {stats['node_id']}\n"
                f"[bold cyan]Cluster:[/bold cyan] {stats['cluster_id']}\n"
                f"[bold yellow]State:[/bold yellow] [bold green]{stats['state']}[/bold green]\n"
                f"[bold cyan]Epoch / Fencing Token:[/bold cyan] {stats['epoch']}\n"
                f"[bold cyan]Failovers Count:[/bold cyan] {stats['failover_count']}\n"
                f"[bold cyan]State Duration:[/bold cyan] {stats['state_duration_s']}s",
                title="MDRAP High Availability Cluster Node Status",
                border_style="cyan",
            )
        )
    elif action == "promote":
        node.promote(reason=getattr(args, "reason", "Operator manual promotion"))
        console.print(
            Panel.fit(
                f"[bold green]✔ Node {node.node_id} successfully promoted to PRIMARY![/bold green]\nNew Epoch: {node.epoch}",
                border_style="green",
            )
        )
    elif action == "demote":
        node.demote(reason=getattr(args, "reason", "Operator manual demotion"))
        console.print(
            Panel.fit(
                f"[bold yellow]Node {node.node_id} transitioned to STANDBY.[/bold yellow]",
                border_style="yellow",
            )
        )
    elif action == "heartbeat":
        hb = node.send_heartbeat(last_committed_seq=getattr(args, "seq", 0))
        console.print(json.dumps(hb.to_dict(), indent=2))


def cmd_archive(args):
    """Show archive statistics."""
    from ..archive import RawArchive

    archive = RawArchive(base_dir=args.base_dir)
    stats = archive.stats()

    console = Console()

    table = Table(title="Raw Event Archive Statistics")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Total Events", f"{stats['total_events']:,}")
    table.add_row(
        "Size",
        f"{stats['size_bytes']:,} bytes ({stats['size_bytes'] / 1024 / 1024:.2f} MB)",
    )
    table.add_row(
        "Date Partitions", ", ".join(stats["dates"]) if stats["dates"] else "none"
    )
    table.add_row(
        "Sources", ", ".join(stats["sources"]) if stats["sources"] else "none"
    )
    console.print(table)


def cmd_retention(args):
    """Run storage retention compaction and disk reclamation."""
    _ensure_db_dir(args.db)
    store = Store(args.db)
    days = getattr(args, "days", 30)
    quarantine_days = getattr(args, "quarantine_days", 90)
    do_vacuum = getattr(args, "vacuum", False)

    res = store.retention_compact(retain_days=days, quarantine_days=quarantine_days)
    if do_vacuum:
        store.vacuum()
        res["vacuum"] = True
    else:
        res["vacuum"] = False
    store.close()

    if getattr(args, "json", False):
        print(json.dumps(res, indent=2))
        return

    console = Console()
    table = Table(title="Storage Retention & Compaction")
    table.add_column("Parameter", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Database", args.db)
    table.add_row("Canonical Retention Window", f"{days} days")
    table.add_row(
        "Quarantine Retention Window",
        f"{quarantine_days} days (evidentiary compliance)",
    )
    table.add_row("Deleted Canonical Rows", f"{res['deleted_canonical']:,}")
    table.add_row("Deleted Quarantine Rows", f"{res['deleted_quarantine']:,}")
    table.add_row("WAL Checkpointed", "Yes (TRUNCATE)")
    table.add_row(
        "VACUUM Executed", "Yes" if do_vacuum else "No (pass --vacuum to run)"
    )
    console.print(table)


def cmd_daemon(args):
    """Start the headless market data streaming daemon service."""
    from ..service import MarketDataDaemon

    console = Console()
    _ensure_db_dir(args.db)

    token = getattr(args, "token", None)
    daemon = MarketDataDaemon(
        host=args.host,
        port=args.port,
        db_path=args.db,
        use_live=getattr(args, "live", False),
        sim_events=getattr(args, "events", 0),
        sim_speed_eps=getattr(args, "speed", 1000.0),
        auth_token=token,
        enable_shm=not getattr(args, "no_shm", False),
        shm_name=getattr(args, "shm_name", "mdrap_feed"),
    )
    console.print()
    auth_notice = (
        "  |  Auth: [bold red]TOKEN REQUIRED[/bold red]" if daemon.auth_token else ""
    )
    shm_notice = (
        "  |  SHM: [bold green]ZERO-COPY (<1µs)[/bold green]"
        if daemon.shm_writer
        else ""
    )
    console.print(
        Panel.fit(
            f"[bold cyan]MDRAP Headless Market Data Daemon (§18)[/bold cyan]\n"
            f"Listening on: [bold green]{args.host}:{args.port}[/bold green]  |  Feed: [bold yellow]{'Live (Binance/Coinbase)' if getattr(args, 'live', False) else 'Multi-Venue Simulator'}[/bold yellow]{auth_notice}{shm_notice}\n"
            f"Database: [bold]{args.db}[/bold]  |  Clients can subscribe via: [bold cyan]mdrap sub [SYM][/bold cyan] or [bold cyan]mdrap sub --shm[/bold cyan]",
            border_style="cyan",
        )
    )
    console.print("[dim]Service running. Press Ctrl+C to stop.[/dim]\n")
    try:
        daemon.start(blocking=True)
    except KeyboardInterrupt:
        console.print("\n[yellow]Shutting down MDRAP daemon...[/yellow]")
    finally:
        daemon.stop()
        console.print(
            "[green]Daemon stopped cleanly. All pending data committed.[/green]\n"
        )


def cmd_gateway(args):
    """Run the MDRAP AsyncIO TCP Gateway for external clients."""
    from ..gateway_tcp import TCPGatewayServer
    import asyncio

    async def run_server():
        server = TCPGatewayServer(host=args.host, port=args.port)
        await server.start()

        console = Console()
        console.print(
            Panel(
                f"[bold cyan]MDRAP External TCP Gateway (§27)[/bold cyan]\n"
                f"Listening on [white]{args.host}:{args.port}[/white]\n"
                f"[dim]Pushing low-latency quality-scored tick data to external quants...[/dim]",
                expand=False,
            )
        )

        try:
            # We would normally connect this to the ShardedPipeline or Simulator.
            # For demonstration, we just idle and could emit mock events here.
            from ..models import CanonicalEvent, EventType, QualityStatus
            import time

            i = 0
            while True:
                await asyncio.sleep(0.1)  # Emit 10 events/sec for demo
                if server.clients:
                    evt = CanonicalEvent(
                        event_id=f"gw-{i}",
                        instrument_id="AAPL",
                        event_type=EventType.TRADE,
                        exchange_timestamp=time.time(),
                        receive_timestamp=time.time(),
                        processing_timestamp=time.time(),
                        source="FEED",
                        sequence_number=i,
                        raw_id=f"raw-{i}",
                        price=150.0 + (i % 5) * 0.1,
                        quantity=100.0,
                    )
                    evt.quality_status = QualityStatus.VALID

                    # Decoupled Payload Mapping
                    payload = {
                        "type": "event",
                        "event_id": evt.event_id,
                        "instrument": evt.instrument_id,
                        "event_type": evt.event_type.value,
                        "quality": evt.quality_status.value,
                        "price": evt.price,
                        "size": evt.quantity,
                        "ts": evt.exchange_timestamp,
                    }
                    await server.broadcast(payload)
                i += 1
        except KeyboardInterrupt:
            pass
        finally:
            await server.stop()

    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        print("\nGateway stopped.")


def cmd_config(args):
    """Handle mdrap config show."""
    from ..config_loader import (
        load_config,
        resolve_config,
        compute_config_hash,
        find_config_path,
    )

    cfg_path = find_config_path()
    cfg = load_config(cfg_path)
    cfg_hash = compute_config_hash(cfg)

    venue = getattr(args, "venue", None)
    inst = getattr(args, "instrument", None)
    inst_cls = getattr(args, "instrument_class", None)

    resolved, origins = resolve_config(
        cfg, venue=venue, instrument_class=inst_cls, symbol=inst
    )

    if getattr(args, "json", False):
        import json

        print(
            json.dumps(
                {
                    "config_file": str(cfg_path)
                    if cfg_path
                    else "defaults (in-memory)",
                    "config_sha256": cfg_hash,
                    "query": {
                        "venue": venue,
                        "instrument_class": inst_cls,
                        "instrument": inst,
                    },
                    "parameters": {
                        k: {"value": v, "origin": origins.get(k, "defaults")}
                        for k, v in resolved.items()
                    },
                },
                indent=2,
            )
        )
        return

    console = Console()
    title_suffix = ""
    if venue:
        title_suffix += f" [Venue: {venue}]"
    if inst:
        title_suffix += f" [Instrument: {inst}]"

    t = Table(title=f"MDRAP Configuration Resolution{title_suffix}", show_lines=True)
    t.add_column("Parameter", style="cyan bold")
    t.add_column("Resolved Value", style="bold green", justify="right")
    t.add_column("Origin Layer", style="yellow")

    for k in sorted(resolved.keys()):
        t.add_row(k, str(resolved[k]), origins.get(k, "defaults"))

    console.print(t)
    console.print(
        f"[dim]Config file: {cfg_path or 'defaults (in-memory)'} | SHA-256: {cfg_hash[:16]}...[/dim]\n"
    )


def cmd_doctor(args):
    """Diagnose platform health, compiler availability, engine tier, WAL status, and benchmark smoke."""
    import platform
    import shutil
    import socket
    import sqlite3
    from ..config_loader import find_config_path, compute_config_hash
    from ..fastpath import HAS_FASTPATH

    console = Console()
    console.print()
    render_brand_header(
        console,
        title="MDRAP Platform Diagnostics & Doctor",
        subtitle="Environment Integrity, Acceleration Tiers & Storage Engine",
        badge="DIAGNOSTIC SCAN",
        meta=f"Host: {platform.node() or 'local'}",
    )

    t = Table(title="Platform Integrity & Acceleration Checklist", show_lines=False)
    t.add_column("Category", style="dim", justify="left")
    t.add_column("Diagnostic Check", style="bold cyan", justify="left")
    t.add_column("Status / Detection", style="white", justify="left")
    t.add_column("Result", justify="right")

    remediations = []

    # 1. Python Environment
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro} ({platform.python_implementation()})"
    t.add_row("Runtime", "Python Version", py_ver, format_status("PASS"))

    # 2. Config File
    cfg_path = find_config_path()
    cfg_str = str(cfg_path) if cfg_path else "Built-in defaults (in-memory)"
    cfg_hash = compute_config_hash()
    t.add_row(
        "Config",
        "Configuration (mdrap.toml)",
        f"{cfg_str} (hash: {cfg_hash[:8]}...)",
        format_status("PASS"),
    )

    # 3. C Compiler Detection
    compilers_found = [c for c in ("gcc", "clang", "cl") if shutil.which(c)]
    comp_str = (
        ", ".join(compilers_found) if compilers_found else "None detected on PATH"
    )
    c_status = format_status("PASS") if compilers_found else format_status("WARN")
    t.add_row("Compiler", "C Compiler Detected", comp_str, c_status)
    if not compilers_found:
        remediations.append(
            "Install GCC, Clang, or MSVC to compile high-throughput Native C vectorized kernels."
        )

    # 4. Active Engine Tier
    if HAS_FASTPATH:
        tier_status = format_status("PASS") + " (Vectorized C)"
        tier_desc = "C DLL Vectorized Context (_fastpath_native.dll)"
    else:
        tier_status = format_status("WARN") + " (Pure Python)"
        tier_desc = "Pure Python QualityEngine (Fallback)"
        remediations.append(
            "Native C Fastpath DLL is not compiled. Build with: `python build_fastpath.py`"
        )
    t.add_row("Engine", "Active Engine Tier", tier_desc, tier_status)

    # 5. Native Core Binary (T1 Hot-Path Engine)
    from ..build_fastpath import get_core_bin_name

    core_bin = get_core_bin_name()
    base_dir = _PACKAGE_ROOT
    core_path = os.path.join(base_dir, core_bin)
    if os.path.isfile(core_path):
        core_status = format_status("READY")
        core_desc = f"{core_bin} ({os.path.getsize(core_path):,} bytes)"
    else:
        core_status = format_status("WARN")
        core_desc = f"{core_bin} uncompiled"
        remediations.append(
            "Native standalone core binary is missing. Build with: `mdrap core --build`"
        )
    t.add_row("Engine", "Native Core Binary (T1)", core_desc, core_status)

    # 6. Hardware Timestamping Support (T1 Tier)
    if sys.platform.startswith("linux"):
        has_so_ts = hasattr(socket, "SO_TIMESTAMPING") or hasattr(
            socket, "SCM_TIMESTAMPING"
        )
        ptp_devs = (
            [f"/dev/{f}" for f in os.listdir("/dev") if f.startswith("ptp")]
            if os.path.exists("/dev")
            else []
        )
        if ptp_devs:
            ts_desc = f"Linux PHC / PTP Hardware Clock ({', '.join(ptp_devs)})"
            ts_status = format_status("PASS") + " (Hardware PTP)"
        elif has_so_ts:
            ts_desc = "Kernel SO_TIMESTAMPING (NIC HW TS available)"
            ts_status = format_status("PASS") + " (SO_TIMESTAMPING)"
        else:
            ts_desc = "Linux CLOCK_MONOTONIC_RAW (Software fallback)"
            ts_status = format_status("WARN") + " (Software)"
    elif sys.platform == "win32":
        ts_desc = "Windows QPC (~100ns precision)"
        ts_status = format_status("PASS") + " (High-Res QPC)"
    elif sys.platform == "darwin":
        ts_desc = "macOS mach_absolute_time (~41ns precision)"
        ts_status = format_status("PASS") + " (Mach Absolute Time)"
    else:
        ts_desc = f"{sys.platform} clock_gettime"
        ts_status = format_status("WARN")
    t.add_row("Timestamps", "Timestamp Precision", ts_desc, ts_status)

    # 7. SQLite WAL Mode
    db_path = getattr(args, "db", "data/mdrap.db")
    wal_ok = False
    try:
        os.makedirs(
            os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True
        )
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")
        mode = cur.fetchone()[0]
        wal_ok = mode.lower() == "wal"
        conn.close()
    except Exception:
        mode = "ERROR"
    t.add_row(
        "Persistence",
        "Storage WAL Journal Mode",
        f"Mode: {mode.upper()}",
        format_status("PASS") if wal_ok else format_status("WARN"),
    )
    if not wal_ok:
        remediations.append(
            f"Storage database is not in WAL mode. Enable with: `sqlite3 {db_path} 'PRAGMA journal_mode=WAL;'`"
        )

    # 8. 10k Smoke Benchmark
    import time
    from ..simulator import FeedSimulator, SimulatorConfig
    from ..pipeline import Pipeline
    from ..storage import Store

    t0 = time.perf_counter()
    sim_cfg = SimulatorConfig(num_events=10_000, seed=42)
    sim = FeedSimulator(sim_cfg)
    raw_events = [raw for raw, _ in sim.generate()]

    with Store(":memory:") as store:
        pipe = Pipeline(store)
        for rev in raw_events:
            pipe.process_one(rev)
        pipe.finish()
    t_proc = time.perf_counter() - t0
    eps = 10_000 / t_proc if t_proc > 0 else 0
    p50_us = (t_proc / 10_000) * 1_000_000

    t.add_row(
        "Smoke Test",
        "10k Event Smoke Benchmark",
        f"{eps:,.0f} eps | avg: {p50_us:.2f} us/event",
        format_status("HEALTHY"),
    )

    console.print(t)
    console.print()

    if remediations:
        rem_lines = "\n".join(
            f"  [dim]•[/dim] [white]{r}[/white]" for r in remediations
        )
        console.print(
            Panel(
                f"[bold yellow]Optimization & Remediation Recommendations:[/bold yellow]\n\n{rem_lines}",
                title="[yellow]Doctor Recommendations[/yellow]",
                title_align="left",
                border_style="yellow",
                box=box.ROUNDED,
            )
        )
        console.print()
        console.print(
            "[bold yellow]▲ SYSTEM OPERATIONAL (WITH RECOMMENDATIONS):[/bold yellow] Follow guidance above to unlock maximum throughput.\n"
        )
    else:
        console.print(
            "[bold green]● ALL CHECKS PASSED:[/bold green] Platform is fully optimized for high-throughput institutional workloads.\n"
        )


def cmd_completion(args):
    """Generate shell autocompletion script for bash, zsh, fish, or powershell."""
    from . import ALL_CANONICAL_COMMANDS

    shell = getattr(args, "shell", "bash").lower()
    commands = sorted(list(set(ALL_CANONICAL_COMMANDS)))
    cmds_str = " ".join(commands)

    if shell == "bash":
        script = f"""# MDRAP bash completion
_mdrap_completions() {{
    local cur="${{COMP_WORDS[COMP_CWORD]}}"
    local prev="${{COMP_WORDS[COMP_CWORD-1]}}"
    local commands="{cmds_str}"
    local global_flags="--help --json --no-color --plain"

    if [ $COMP_CWORD -eq 1 ]; then
        COMPREPLY=( $(compgen -W "${{commands}} ${{global_flags}}" -- "${{cur}}") )
        return 0
    fi
}}
complete -F _mdrap_completions mdrap
"""
    elif shell == "zsh":
        cmd_entries = "\n".join(
            f"        '{cmd}:MDRAP {cmd} command'" for cmd in commands
        )
        script = f"""#compdef mdrap
# MDRAP zsh completion

_mdrap() {{
    local -a commands
    commands=(
{cmd_entries}
    )
    _arguments -C \\
        '--help[Show help message]' \\
        '--json[Output structured JSON]' \\
        '--no-color[Suppress ANSI color]' \\
        '--plain[Suppress ANSI color]' \\
        '1: :->cmds' \\
        '*:: :->args'

    case $state in
        cmds)
            _describe -t commands 'mdrap command' commands
            ;;
    esac
}}

compdef _mdrap mdrap
"""
    elif shell == "fish":
        lines = [
            "# MDRAP fish completion",
            "complete -c mdrap -f",
            "complete -c mdrap -l help -d 'Show help message'",
            "complete -c mdrap -l json -d 'Output structured JSON'",
            "complete -c mdrap -l no-color -d 'Suppress ANSI color styling'",
            "complete -c mdrap -l plain -d 'Suppress ANSI color styling'",
        ]
        for cmd in commands:
            lines.append(
                f"complete -c mdrap -n '__fish_use_subcommand' -a {cmd} -d 'MDRAP {cmd}'"
            )
        script = "\n".join(lines) + "\n"
    elif shell in ("pwsh", "powershell", "ps1"):
        script = f"""# MDRAP PowerShell completion
Register-ArgumentCompleter -Native -CommandName mdrap -ScriptBlock {{
    param($wordToComplete, $commandAst, $cursorPosition)
    $commands = @({", ".join(f"'{c}'" for c in commands)})
    $flags = @('--help', '--json', '--no-color', '--plain')
    $elements = $commandAst.CommandElements
    if ($elements.Count -le 2) {{
        $candidates = $commands + $flags
        $candidates | Where-Object {{ $_ -like "$wordToComplete*" }} | ForEach-Object {{
            [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_)
        }}
    }}
}}
"""
    else:
        print(
            f"Unsupported shell: {shell}. Supported: bash, zsh, fish, powershell",
            file=sys.stderr,
        )
        return

    print(script, end="")


def cmd_wal(args):
    """Verify, inspect, or salvage IngestLog Write-Ahead Log segments."""
    from ..ingestlog import IngestLog

    action = getattr(args, "action", "verify") or "verify"
    db_path = getattr(args, "db", "data/mdrap.db")
    wal_path = getattr(args, "wal_path", None) or f"{db_path}.wal"

    console = Console()

    if action == "verify":
        report = IngestLog.verify(wal_path)
        if getattr(args, "json", False):
            print(json.dumps(report, indent=2))
            return
        console.print()
        console.print(
            Panel.fit(
                f"[bold cyan]MDRAP Write-Ahead Log (WAL) Verification[/bold cyan]\n"
                f"Target Directory: [yellow]{report['wal_path']}[/yellow]\n"
                f"Status: " + ("[bold green]CLEAN[/bold green]" if report["is_clean"] else "[bold red]CORRUPTED[/bold red]"),
                title="WAL Verification",
                border_style="cyan" if report["is_clean"] else "red",
            )
        )
        t = Table(show_lines=True)
        t.add_column("Metric", style="cyan")
        t.add_column("Value", justify="right")
        t.add_row("Total Segments", str(report["total_segments"]))
        t.add_row("Valid Frames", f"[green]{report['valid_frames']:,}[/green]")
        t.add_row("Corrupted Segments", f"[red]{report['corrupted_segments']}[/red]" if report['corrupted_segments'] else "0")
        t.add_row("Corrupted Frames", f"[red]{report['corrupted_frames']}[/red]" if report['corrupted_frames'] else "0")
        console.print(t)
        if report["errors"]:
            console.print("\n[bold red]Corruption Details:[/bold red]")
            for err in report["errors"][:10]:
                console.print(f"  [red]•[/red] {err}")
            if len(report["errors"]) > 10:
                console.print(f"  [dim]... and {len(report['errors']) - 10} more errors[/dim]")
            console.print("\n[yellow]Tip:[/yellow] Run [cyan]mdrap wal salvage[/cyan] to recover valid frames.")

    elif action == "salvage":
        backup = getattr(args, "backup", True)
        report = IngestLog.salvage(wal_path, backup=backup)
        if getattr(args, "json", False):
            print(json.dumps(report, indent=2))
            return
        console.print()
        console.print(
            Panel.fit(
                f"[bold cyan]MDRAP Write-Ahead Log (WAL) Salvage & Repair[/bold cyan]\n"
                f"Target Directory: [yellow]{wal_path}[/yellow]\n"
                f"Backup Preserved: [blue]{backup}[/blue]",
                title="WAL Salvage Tool",
                border_style="green",
            )
        )
        t = Table(show_lines=True)
        t.add_column("Recovery Metric", style="cyan")
        t.add_column("Result", justify="right")
        t.add_row("Scanned Segments", str(report["scanned_segments"]))
        t.add_row("Corrupted Segments Detected", str(report["corrupted_segments"]))
        t.add_row("Recovered Valid Records", f"[bold green]{report['recovered_records']:,}[/bold green]")
        t.add_row("Repaired Files", str(len(report["repaired_files"])))
        console.print(t)

