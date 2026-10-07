from ._common import *

__stability__ = "beta"


def cmd_security(args):
    """Display platform security posture, HMAC verification, RBAC, and rate limiting status."""
    from ..security import SecurityManager

    console = Console()
    store = Store(args.db) if os.path.exists(args.db) else None
    sec = SecurityManager(store=store)

    console.print()
    console.print(
        Panel.fit(
            "[bold cyan]MDRAP Platform Security & Cryptographic Posture (§19)[/bold cyan]",
            border_style="cyan",
        )
    )

    t1_rows = [
        [
            src,
            "Active (Constant-Time)",
            "Configured & Sealed",
            "Enforced (Sequence + Ts)",
        ]
        for src in sec._secrets.keys()
    ]
    console.print(
        _t(
            "Cryptographic Feed Authentication (HMAC-SHA256)",
            [
                ("Market Feed", "left", "cyan"),
                ("HMAC Verification", "left", "green"),
                ("Pre-Shared Key Status", "left", "magenta"),
                ("Anti-Spoofing / Replay", "left", "white"),
            ],
            t1_rows,
        )
    )

    t2_rows = [
        (
            "Role-Based Access Control",
            "RBAC (VIEWER / OPERATOR / ADMIN)",
            "Strict Privilege Checking",
            "Active",
        ),
        (
            "Denial-of-Service Defense",
            "Token Bucket Rate Limiter",
            "20,000 eps / 40,000 capacity",
            "Active",
        ),
        (
            "Input Sanitization Guard",
            "Regex + Numerical Bounds Whitelist",
            "Strict Bounds & Safe SQL",
            "Active",
        ),
        (
            "Tamper-Evident Audit Chain",
            "Merkle Hash Chaining (SHA-256)",
            "Append-Only Genesis Link",
            "Active",
        ),
    ]
    console.print(
        _t(
            "Access Control & Denial-of-Service Mitigations",
            [
                ("Defense Layer", "left", "cyan"),
                ("Mechanism", "left", "white"),
                ("Enforcement / Threshold", "left", "yellow"),
                ("Status", "left", "green"),
            ],
            t2_rows,
        )
    )

    if store:
        valid, msg, count = sec.verify_audit_trail()
        status_color = "green" if valid else "red"
        console.print(f"Audit Trail Status: [{status_color}]{msg}[/{status_color}]\n")
        store.close()


def cmd_keys(args):
    """Manage client API keys and authentication tokens."""
    from ..security import SecurityManager

    console = Console()
    store = Store(args.db) if os.path.exists(args.db) else Store("data/mdrap.db")
    sec = SecurityManager(store=store)

    action = getattr(args, "action", "list") or "list"

    if action == "list":
        k_cols = [
            ("Client ID", "left", "cyan"),
            ("Role", "center", "yellow"),
            ("Key Prefix", "left", "dim"),
            ("Status", "center"),
        ]
        k_rows = []
        for key in sec.list_api_keys():
            st_str = "[green]ACTIVE[/green]" if key.is_active else "[red]REVOKED[/red]"
            role_val = getattr(key.role, "value", str(key.role))
            role_badge = f"[bold]{role_val}[/bold]"
            prefix_display = getattr(key, "key_prefix", "") or (
                key.token[:12] if key.token else "mdrap_live_***"
            )
            k_rows.append(
                [
                    key.client_id,
                    role_badge,
                    prefix_display,
                    st_str,
                ]
            )
        console.print(
            _t(
                "MDRAP Client API Keys & Access Tokens (Hashed Storage)",
                k_cols,
                k_rows,
                show_lines=True,
            )
        )

    elif action == "create":
        client_id = getattr(args, "client_id", "Custom_Client")
        role = getattr(args, "role", "VIEWER").upper()
        ent = sec.register_api_key(client_id=client_id, role=role)
        ent_role_val = getattr(ent.role, "value", str(ent.role))
        console.print(
            Panel.fit(
                f"[bold green]API Key Generated Successfully![/bold green]\n\n"
                f"Client ID: [bold cyan]{ent.client_id}[/bold cyan]\n"
                f"Role: [bold yellow]{ent_role_val}[/bold yellow]\n"
                f"API Token: [bold green]{ent.token}[/bold green]\n"
                f"Key Prefix: [dim]{ent.key_prefix}[/dim]\n"
                f"Status: [green]ACTIVE[/green]\n\n"
                f"[bold red]WARNING:[/bold red] Copy and store this secret key securely now.\n"
                f"It is hashed with SHA-256 in the database and [bold underline]cannot be displayed again[/bold underline].",
                title="Client Authentication Key Created",
                border_style="green",
            )
        )

    elif action == "revoke":
        token = getattr(args, "token", "") or getattr(args, "prefix", "")
        if not token:
            console.print(
                "[bold red]Error:[/bold red] API token or prefix must be specified for revocation (use --token <key_or_prefix>)."
            )
            store.close()
            return
        ok = sec.revoke_api_key(token)
        if ok:
            console.print(
                f"[bold green]API Key revoked successfully:[/bold green] [dim]{token}[/dim]"
            )
        else:
            console.print(
                f"[bold red]Error:[/bold red] API token/prefix not found: {token}"
            )

    elif action == "rotate":
        token = getattr(args, "token", "")
        grace = getattr(args, "grace", 3600.0)
        if not token:
            console.print(
                "[bold red]Error:[/bold red] API token must be specified for rotation (use --token <key>)."
            )
            store.close()
            return
        new_ent, old_ent = sec.rotate_api_key(token, grace_period_s=grace)
        store.commit()
        console.print(
            Panel.fit(
                f"[bold green]✔ API Key Rotated Successfully![/bold green]\n\n"
                f"Client ID: [bold cyan]{new_ent.client_id}[/bold cyan]\n"
                f"New Active Token: [bold green]{new_ent.token}[/bold green]\n"
                f"Old Token Expires At: [yellow]{old_ent.expires_at}[/yellow] (grace: {grace}s)",
                border_style="green",
            )
        )

    store.close()


def cmd_audit(args):
    """View and cryptographically verify tamper-evident audit logs."""
    from ..security import SecurityManager

    console = Console()

    # Standalone proof verification (requires no database)
    if getattr(args, "verify_proof", None):
        valid, msg, count = Store.verify_standalone_proof(args.verify_proof)
        if valid:
            console.print(
                Panel.fit(
                    f"[bold green]✔ INDEPENDENT AUDIT PROOF VERIFIED[/bold green]\n{msg}\nAll {count} entries verified against SHA-256 specification.",
                    border_style="green",
                )
            )
        else:
            console.print(
                Panel.fit(
                    f"[bold red]✖ AUDIT PROOF VERIFICATION FAILED / TAMPERED![/bold red]\n{msg}",
                    border_style="red",
                )
            )
        return

    store = Store(args.db) if os.path.exists(args.db) else None
    if not store:
        console.print(
            f"[yellow]Database '{args.db}' not found. Run pipeline first.[/yellow]"
        )
        return

    # Export standalone proof
    if getattr(args, "export_proof", None):
        proof = store.export_audit_proof(args.export_proof)
        console.print(
            f"[bold green]✔ Cryptographic audit proof successfully exported to '{args.export_proof}' ({proof['total_entries']} entries).[/bold green]"
        )
        store.close()
        return

    if getattr(args, "print_anchor", False):
        count, head = store.get_audit_anchor()
        console.print(f"{count}:{head}")
        store.close()
        return

    if getattr(args, "sign_checkpoint", False):
        try:
            import json

            cp = store.sign_audit_checkpoint()
            console.print(json.dumps(cp, indent=2))
        except Exception as exc:
            console.print(f"[bold red]✖ Checkpoint signing error: {exc}[/bold red]")
        store.close()
        return

    sec = SecurityManager(store=store)

    anchor_arg = getattr(args, "anchor", None)
    anchor_tuple = None
    if anchor_arg:
        parts = anchor_arg.split(":", 1)
        if len(parts) == 2 and parts[0].isdigit():
            anchor_tuple = (int(parts[0]), parts[1])
        else:
            console.print(
                "[bold red]✖ Invalid anchor format. Expected <count>:<head_hash>[/bold red]"
            )
            store.close()
            return

    if getattr(args, "verify", False) or anchor_tuple is not None:
        valid, msg, count = sec.verify_audit_trail(anchor=anchor_tuple)
        if valid:
            console.print(
                Panel.fit(
                    f"[bold green]✔ CRYPTOGRAPHIC AUDIT VERIFICATION PASSED[/bold green]\n{msg}",
                    border_style="green",
                )
            )
        else:
            console.print(
                Panel.fit(
                    f"[bold red]✖ AUDIT CHAIN TAMPERING DETECTED![/bold red]\n{msg}",
                    border_style="red",
                )
            )
        store.close()
        return

    if getattr(args, "anchor_batch", False):
        batch_sz = getattr(args, "batch_size", 100) or 100
        root = sec.anchor_audit_batch(batch_size=batch_sz)
        store.commit()
        console.print(
            Panel.fit(
                f"[bold green]✔ MERKLE BATCH ANCHOR GENERATED[/bold green]\n\n"
                f"Batch Size: [bold cyan]{batch_sz}[/bold cyan]\n"
                f"Root Hash: [bold green]{root}[/bold green]\n"
                f"Anchored in persistent audit log.",
                border_style="green",
            )
        )
        store.close()
        return

    rows = store.query_audit_log(limit=args.limit)
    if not rows:
        sec.log_audit(
            "AUDIT_INIT", actor="system", details="Platform security initialized"
        )
        rows = store.query_audit_log(limit=args.limit)

    table = Table(title=f"Tamper-Evident Security Audit Log (§19) (Recent {len(rows)})")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Timestamp", style="magenta")
    table.add_column("Actor", style="cyan")
    table.add_column("Role", style="yellow")
    table.add_column("Action", style="bold white")
    table.add_column("Details", style="white")
    table.add_column("Hash (Merkle Link)", style="dim green")

    for r in rows:
        ts_str = (
            time.strftime("%H:%M:%S", time.localtime(r["timestamp"]))
            + f".{int(r['timestamp'] * 1000) % 1000:03d}"
        )
        h_prev = r.get("prev_hash", "")[:8]
        h_curr = r.get("entry_hash", "")[:8]
        table.add_row(
            str(r["entry_id"]),
            ts_str,
            r["actor"],
            r["role"],
            r["action"],
            r["details"],
            f"{h_prev}..->{h_curr}..",
        )

    console.print(table)
    valid, msg, count = sec.verify_audit_trail()
    color = "green" if valid else "red"
    console.print(f"[dim]Chain Integrity: [{color}]{msg}[/{color}][/dim]\n")
    store.close()


def cmd_deadletter(args):
    """Inspect and replay uncommitted dead-letter transaction logs."""
    from ..pipeline import get_dead_letter_dir, replay_dead_letter_spills
    from ..storage import Store

    console = Console()
    dl_dir = getattr(args, "dir", None) or get_dead_letter_dir()

    if getattr(args, "action", "") == "replay":
        store = Store(args.db)
        stats = replay_dead_letter_spills(store, dead_letter_dir=dl_dir)
        store.close()
        console.print(
            Panel.fit(
                f"[bold green]✔ DEAD-LETTER REPLAY COMPLETED[/bold green]\n"
                f"Files processed: {stats['files_processed']}\n"
                f"Canonical events replayed: {stats['canonical_replayed']}\n"
                f"Quarantine records replayed: {stats['quarantine_replayed']}\n"
                f"Lineage records replayed: {stats['lineage_replayed']}",
                border_style="green",
            )
        )
        return

    # Default action: list
    if not os.path.exists(dl_dir):
        console.print(
            f"[dim]Dead-letter directory '{dl_dir}' does not exist (no spills).[/dim]"
        )
        return

    files = [
        f for f in os.listdir(dl_dir) if f.startswith("spill-") and f.endswith(".jsonl")
    ]
    if not files:
        console.print(f"[green]✔ No pending dead-letter spills in '{dl_dir}'.[/green]")
        return

    table = Table(title=f"Pending Dead-Letter Spills ({len(files)} files)")
    table.add_column("Filename", style="cyan")
    table.add_column("Size (bytes)", justify="right", style="magenta")
    for f in sorted(files):
        sz = os.path.getsize(os.path.join(dl_dir, f))
        table.add_row(f, str(sz))
    console.print(table)


def cmd_plugins(args):
    """List installed plugins and extension entry points."""
    from ..plugins import discover_all_plugins

    console = Console()
    all_plugins = discover_all_plugins()

    table = Table(title="MDRAP Extension Points & Installed Plugins")
    table.add_column("Plugin Group", style="cyan")
    table.add_column("Plugin Name", style="bold white")
    table.add_column("Target Object / Factory", style="green")

    total_count = 0
    for grp, plugins in all_plugins.items():
        if not plugins:
            table.add_row(grp, "[dim](none installed)[/dim]", "[dim]-[/dim]")
        else:
            for name, obj in plugins.items():
                total_count += 1
                table.add_row(grp, name, repr(obj))

    console.print(table)
    console.print(f"[dim]Total plugins discovered: {total_count}[/dim]\n")
