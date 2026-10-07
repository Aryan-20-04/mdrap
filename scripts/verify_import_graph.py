#!/usr/bin/env python3
"""Assert MDRAP package ownership, relative imports, and shim separation."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "src" / "mdrap"
CORE_HELPER = ROOT / "src" / "mdrap_build.py"
CONTRIB_HELPER = ROOT / "contrib" / "src" / "mdrap_contrib_build.py"


def module_set(helper: Path) -> set[str]:
    tree = ast.parse(helper.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_CORE_MODULES"
            for target in node.targets
        ):
            return set(ast.literal_eval(node.value))
    raise AssertionError(f"_CORE_MODULES missing from {helper}")


def main() -> None:
    core = module_set(CORE_HELPER)
    contrib_core_copy = module_set(CONTRIB_HELPER)
    assert core - {"__init__"} == contrib_core_copy, "core/contrib module filters diverged"
    package_modules = {path.stem for path in PACKAGE.glob("*.py") if path.name != "__init__.py"}
    contrib = package_modules - core
    assert core - {"__init__"} | contrib == package_modules
    contrib_sources = {path.stem for path in (ROOT / "contrib" / "src" / "mdrap").glob("*.py")}
    assert contrib_sources == contrib, (
        f"contrib source ownership differs: extra={sorted(contrib_sources-contrib)}, "
        f"missing={sorted(contrib-contrib_sources)}"
    )

    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "__init__", f"legacy package import in {path}"
                    assert alias.name.split(".", 1)[0] not in package_modules, (
                        f"bare internal import {alias.name!r} in {path}"
                    )
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                assert node.module.split(".", 1)[0] not in package_modules, (
                    f"bare internal import from {node.module!r} in {path}"
                )
            elif isinstance(node, ast.Attribute) and node.attr == "path":
                if isinstance(node.value, ast.Name) and node.value.id in {"sys", "_sys"}:
                    raise AssertionError(f"sys.path manipulation/reference in package module {path}")

    shim_sources = {
        path.name: path
        for path in (ROOT / "src").glob("*.py")
        if path.name not in {"__init__.py", "mdrap_build.py"}
    }
    compat_sources = {path.name: path for path in (ROOT / "compat" / "src").glob("*.py")}
    assert shim_sources.keys() == compat_sources.keys(), "compat distribution shim inventory differs"
    for name, source in shim_sources.items():
        assert source.read_bytes() == compat_sources[name].read_bytes(), f"stale shim copy: {name}"

    root_cli = ROOT / "src" / "mdrap" / "cli"
    contrib_cli = ROOT / "contrib" / "src" / "mdrap" / "cli"
    root_cli_files = {path.relative_to(root_cli): path for path in root_cli.rglob("*.py")}
    contrib_cli_files = {path.relative_to(contrib_cli): path for path in contrib_cli.rglob("*.py")}
    assert root_cli_files.keys() == contrib_cli_files.keys(), "source and contrib CLI layouts differ"
    for relative, path in root_cli_files.items():
        assert path.read_bytes() == contrib_cli_files[relative].read_bytes(), f"CLI source copy differs: {relative}"
    required_groups = {"core.py", "security.py", "operations.py", "market.py", "integrations.py"}
    assert required_groups <= {path.name for path in root_cli_files.values()}, "CLI command group missing"
    command_count = 0
    for path in root_cli_files.values():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        command_count += sum(
            isinstance(node, ast.FunctionDef) and node.name.startswith("cmd_") and node.name != "cmd_shell"
            for node in tree.body
        )
        if path.name == "__init__.py":
            facade_commands = {
                node.name for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name.startswith("cmd_")
            }
            assert facade_commands == {"cmd_shell"}, f"CLI facade still owns command handlers: {facade_commands}"
    assert command_count >= 50, f"command handlers were not decomposed into groups: {command_count}"

    print(
        f"Import graph verified: {len(core) - 1} core modules, {len(contrib)} contrib modules, "
        f"{len(shim_sources)} opt-in shims; {command_count} handlers in five CLI groups; internal imports are package-relative."
    )


if __name__ == "__main__":
    main()
