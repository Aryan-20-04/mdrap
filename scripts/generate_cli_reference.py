"""Script to generate docs/cli-reference.md dynamically from mdrap live argparse."""

import sys
from pathlib import Path
import argparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from mdrap.cli import build_parser

def generate_cli_reference():
    parser = build_parser()
    sub_action = [a for a in parser._actions if a.dest == "command"][0]

    # Map name -> help string from _choices_actions
    help_map = {}
    for ca in getattr(sub_action, "_choices_actions", []):
        if ca.dest and ca.help:
            help_map[ca.dest] = ca.help

    seen = set()
    commands = []

    for name, sp in sub_action.choices.items():
        if sp in seen:
            continue
        seen.add(sp)
        prog = sp.prog.split()[-1]
        aliases = sorted([k for k, v in sub_action.choices.items() if v is sp and k != prog])
        cmd_help = help_map.get(prog, sp.description or "")
        commands.append((prog, aliases, sp, cmd_help))

    # Sort commands alphabetically by primary command name
    commands.sort(key=lambda x: x[0])

    lines = []
    lines.append("# MDRAP CLI Reference Manual")
    lines.append("")
    lines.append("Complete, authoritative reference for the `mdrap` command-line interface, generated directly from the live `argparse` definition.")
    lines.append("")
    lines.append(f"Total commands documented: **{len(commands)}**.")
    lines.append("")
    lines.append("## Global Options")
    lines.append("")
    lines.append("| Flag | Description |")
    lines.append("| --- | --- |")
    lines.append("| `--json` | Output structured JSON instead of formatted tables. |")
    lines.append("| `--pretty` | Force rich terminal UI formatting even in non-interactive/redirected contexts. |")
    lines.append("| `--no-color`, `--plain` | Suppress ANSI colors and styling (honors `NO_COLOR=1`). |")
    lines.append("| `-h`, `--help` | Display help and exit. |")
    lines.append("")
    lines.append("## Command Index")
    lines.append("")
    lines.append("| Command | Aliases | Description |")
    lines.append("| --- | --- | --- |")
    for prog, aliases, sp, cmd_help in commands:
        alias_str = ", ".join(f"`{a}`" for a in aliases) if aliases else "*none*"
        short_desc = cmd_help.strip().split("\n")[0] if cmd_help else "Execute " + prog
        lines.append(f"| [`{prog}`](#{prog}) | {alias_str} | {short_desc} |")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Subcommand Details")
    lines.append("")

    for prog, aliases, sp, cmd_help in commands:
        lines.append(f"### `{prog}`")
        lines.append("")
        desc = (cmd_help or sp.description or "").strip()
        if desc:
            lines.append(f"**Description:** {desc}")
            lines.append("")
        if aliases:
            lines.append(f"**Aliases:** " + ", ".join(f"`{a}`" for a in aliases))
            lines.append("")

        lines.append(f"**Usage:** `mdrap {prog} [OPTIONS]`")
        lines.append("")

        # Extract arguments
        pos_args = []
        opt_args = []

        for action in sp._actions:
            if isinstance(action, argparse._HelpAction):
                continue
            if getattr(action, "default", None) is argparse.SUPPRESS:
                continue

            opts = ", ".join(f"`{opt}`" for opt in action.option_strings)
            help_text = (action.help or "").strip()
            default_val = action.default
            if default_val is not None and default_val != "" and not isinstance(default_val, bool):
                default_str = f" (default: `{default_val}`)"
            else:
                default_str = ""

            if not action.option_strings:
                # Positional
                name = action.dest
                pos_args.append((name, help_text + default_str))
            else:
                opt_args.append((opts, help_text + default_str))

        if pos_args:
            lines.append("#### Positional Arguments")
            lines.append("")
            lines.append("| Argument | Description |")
            lines.append("| --- | --- |")
            for name, h in pos_args:
                lines.append(f"| `{name}` | {h} |")
            lines.append("")

        if opt_args:
            lines.append("#### Options")
            lines.append("")
            lines.append("| Option | Description |")
            lines.append("| --- | --- |")
            for opts, h in opt_args:
                lines.append(f"| {opts} | {h} |")
            lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)

if __name__ == "__main__":
    out_path = Path(__file__).resolve().parent.parent / "docs" / "cli-reference.md"
    content = generate_cli_reference()
    out_path.write_text(content, encoding="utf-8")
    print(f"Wrote {out_path} ({len(content)} bytes)")
