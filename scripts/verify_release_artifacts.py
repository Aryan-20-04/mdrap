#!/usr/bin/env python3
"""Build and inspect MDRAP core/contrib release artifacts."""

from __future__ import annotations

import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
VERSION = "3.0.0"


def build(project: Path, distribution: str) -> tuple[Path, Path]:
    subprocess.run(
        [sys.executable, "-m", "build", "--no-isolation", "--outdir", str(DIST)],
        cwd=project,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    normalized = distribution.replace("-", "_")
    wheel = next(DIST.glob(f"{normalized}-{VERSION}-*.whl"))
    sdist = DIST / f"{normalized}-{VERSION}.tar.gz"
    assert sdist.is_file(), f"missing sdist {sdist}"
    return wheel, sdist


def check_sdist(path: Path, required: set[str]) -> None:
    with tarfile.open(path, "r:gz") as archive:
        members = {Path(item).as_posix() for item in archive.getnames()}
    prefix = f"{path.stem.removesuffix('.tar')}/"
    missing = {prefix + item for item in required} - members
    assert not missing, f"{path.name} missing source entries: {sorted(missing)}"
    if "mdrap_core" in path.name:
        relative = {item[len(prefix):] for item in members if item.startswith(prefix)}
        allowed_modules = {
            "__init__", "_version", "analytics", "archive", "audit_format", "bbo", "client", "config", "fastpath", "gateway", "metrics", "models", "pipeline", "plugins", "protocol", "protocols", "quality", "quarantine", "reconciliation", "rules", "sbe", "security", "shm", "storage", "symbology", "watchdog",
        }
        expected_source_py = {
            f"src/mdrap/{name}.py" for name in allowed_modules
        } | {
            f"src/mdrap/adapters/{path.name}" for path in (ROOT / "src" / "mdrap" / "adapters").glob("*.py")
        } | {"src/mdrap_build.py"}
        actual_source_py = {name for name in relative if name.startswith("src/") and name.endswith(".py")}
        assert actual_source_py == expected_source_py, (
            f"core sdist source ownership differs: unexpected={sorted(actual_source_py-expected_source_py)}, "
            f"missing={sorted(expected_source_py-actual_source_py)}"
        )
        forbidden_prefixes = {"docs/", "tests/", "examples/", "personas/", "fuzz/"}
        assert not {name for name in relative if any(name.startswith(prefix) for prefix in forbidden_prefixes)}, (
            "core sdist contains non-package project trees"
        )
    print(f"{path.name}: inspected {len(members)} entries; required sources present")


def check_wheel(path: Path, required: set[str], allowed_top: set[str]) -> None:
    with zipfile.ZipFile(path) as archive:
        files = set(archive.namelist())
    missing = required - files
    assert not missing, f"{path.name} missing wheel entries: {sorted(missing)}"
    top = {
        name.split("/", 1)[0]
        for name in files
        if not name.split("/", 1)[0].endswith(".dist-info")
    }
    assert top == allowed_top, f"{path.name} has unexpected top-level entries {top}"
    if "contrib" in path.name:
        entry_file = next(name for name in files if name.endswith(".dist-info/entry_points.txt"))
        with zipfile.ZipFile(path) as archive:
            entry_points = archive.read(entry_file).decode("utf-8")
        assert "mdrap = mdrap.cli:main" in entry_points, "CLI entry point missing"
    print(f"{path.name}: inspected {len(files)} entries; package contents verified")


def main() -> None:
    source_version = (ROOT / "src" / "mdrap" / "_version.py").read_text(encoding="utf-8")
    assert f'__version__ = "{VERSION}"' in source_version, "release script version is stale"
    contrib_config = (ROOT / "contrib" / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{VERSION}"' in contrib_config, "contrib/core versions differ"
    compat_config = (ROOT / "compat" / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{VERSION}"' in compat_config, "compat/core versions differ"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "verify_import_graph.py")], cwd=ROOT, check=True)
    DIST.mkdir(exist_ok=True)
    core_wheel, core_sdist = build(ROOT, "mdrap-core")
    contrib_dir = ROOT / "contrib"
    contrib_wheel, contrib_sdist = build(contrib_dir, "mdrap-contrib")
    compat_dir = ROOT / "compat"
    compat_wheel, compat_sdist = build(compat_dir, "mdrap-compat")

    check_wheel(
        core_wheel,
        {
            "mdrap/__init__.py", "mdrap/client.py", "mdrap/models.py",
            "mdrap/pipeline.py", "mdrap/plugins.py", "mdrap/quarantine.py",
            "mdrap/rules.def", "mdrap/adapters/reference.py",
        },
        {"mdrap"},
    )
    with zipfile.ZipFile(core_wheel) as archive:
        core_wheel_modules = {name for name in archive.namelist() if name.endswith(".py") and name.startswith("mdrap/")}
    expected_core_modules = {
        f"mdrap/{path.name}" for path in (ROOT / "src" / "mdrap").glob("*.py")
        if path.stem in {"__init__", "_version", "analytics", "archive", "audit_format", "bbo", "client", "config", "fastpath", "gateway", "metrics", "models", "pipeline", "plugins", "protocol", "protocols", "quality", "quarantine", "reconciliation", "rules", "sbe", "security", "shm", "storage", "symbology", "watchdog"}
    } | {f"mdrap/adapters/{path.name}" for path in (ROOT / "src" / "mdrap" / "adapters").glob("*.py")}
    assert core_wheel_modules == expected_core_modules, f"core wheel module ownership differs: unexpected={sorted(core_wheel_modules-expected_core_modules)}, missing={sorted(expected_core_modules-core_wheel_modules)}"
    compat_modules = {path.stem for path in (compat_dir / "src").glob("*.py")}
    check_wheel(
        compat_wheel,
        {f"{name}.py" for name in compat_modules},
        {f"{name}.py" for name in compat_modules},
    )
    check_wheel(
        contrib_wheel,
        {
            "mdrap/cli/__init__.py", "mdrap/cli/__main__.py",
            "mdrap/cli/core.py", "mdrap/cli/operations.py",
            "mdrap/portfolio.py", "mdrap/terminal_display.py",
        },
        {"mdrap"},
    )
    with zipfile.ZipFile(contrib_wheel) as archive:
        contrib_wheel_modules = {name for name in archive.namelist() if name.endswith(".py") and name.startswith("mdrap/")}
    expected_contrib_modules = {
        path.relative_to(contrib_dir / "src").as_posix()
        for path in (contrib_dir / "src" / "mdrap").rglob("*.py")
    }
    assert contrib_wheel_modules == expected_contrib_modules, f"contrib wheel module ownership differs: unexpected={sorted(contrib_wheel_modules-expected_contrib_modules)}, missing={sorted(expected_contrib_modules-contrib_wheel_modules)}"
    check_sdist(
        core_sdist,
        {"pyproject.toml", "README.md", "src/mdrap/__init__.py", "src/mdrap/pipeline.py", "src/mdrap/rules.def"},
    )
    check_sdist(
        contrib_sdist,
        {"pyproject.toml", "README.md", "src/mdrap/cli/__init__.py", "src/mdrap/cli/market.py", "src/mdrap/portfolio.py"},
    )
    check_sdist(compat_sdist, {"pyproject.toml", "README.md", "src/models.py", "src/_version.py"})
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify_package_install.py"), str(core_wheel), str(contrib_wheel), str(compat_wheel)],
        cwd=ROOT,
        check=True,
    )
    print("Core and contrib release artifact gates passed.")


if __name__ == "__main__":
    main()
