#!/usr/bin/env python3
"""Install a wheel into an isolated target and test imports outside the repo."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    if len(sys.argv) not in (3, 4):
        raise SystemExit("usage: verify_package_install.py CORE_WHEEL CONTRIB_WHEEL [COMPAT_WHEEL]")
    wheels = [Path(value).resolve() for value in sys.argv[1:]]
    for wheel in wheels:
        if not wheel.is_file() or wheel.suffix != ".whl":
            raise SystemExit(f"wheel not found: {wheel}")

    with tempfile.TemporaryDirectory(prefix="mdrap-wheel-check-") as temp_name:
        temp = Path(temp_name)
        target = temp / "install"
        app = temp / "application"
        target.mkdir()
        app.mkdir()
        (app / "models.py").write_text(
            "APPLICATION_SENTINEL = True\n", encoding="utf-8"
        )
        core_target = temp / "core-only-install"
        core_app = temp / "core-only-application"
        core_target.mkdir()
        core_app.mkdir()
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--no-deps", "--no-cache-dir", "--target", str(core_target), str(wheels[0])],
            check=True,
        )
        core_env = os.environ.copy()
        core_env["PYTHONPATH"] = str(core_target)
        core_code = (
            "import importlib, importlib.util, pkgutil, mdrap; "
            "mods = [m.name for m in pkgutil.iter_modules(mdrap.__path__) if not m.ispkg]; "
            "[importlib.import_module('mdrap.' + name) for name in mods]; "
            "import mdrap.adapters.reference, mdrap.adapters.template, mdrap.plugins; "
            "assert importlib.util.find_spec('mdrap.cli') is None; "
            "assert importlib.util.find_spec('mdrap.portfolio') is None"
        )
        subprocess.run([sys.executable, "-S", "-c", core_code], cwd=core_app, env=core_env, check=True)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--no-deps",
                "--no-cache-dir",
                "--target",
                str(target),
                *(str(wheel) for wheel in wheels),
            ],
            check=True,
        )

        env = os.environ.copy()
        env["PYTHONPATH"] = str(target)
        env["MDRAP_WHEEL_TARGET"] = str(target)
        code = (
            "import importlib.util, pathlib; "
            "import mdrap, mdrap.models, mdrap.plugins, mdrap.cli, mdrap.portfolio, models; "
            "target = pathlib.Path(mdrap.__file__).resolve().parents[1]; "
            "assert target == pathlib.Path(__import__('os').environ['MDRAP_WHEEL_TARGET']).resolve(); "
            "assert models.APPLICATION_SENTINEL; "
            "assert pathlib.Path(models.__file__).resolve().parent == pathlib.Path.cwd(); "
            "assert pathlib.Path(mdrap.models.__file__).resolve().is_relative_to(target); "
            "assert importlib.util.find_spec('mdrap.plugins') is not None; "
            "assert importlib.util.find_spec('mdrap.cli') is not None; "
            "assert importlib.util.find_spec('mdrap.portfolio') is not None"
        )
        subprocess.run([sys.executable, "-S", "-c", code], cwd=app, env=env, check=True)
        subprocess.run([sys.executable, "-S", "-m", "mdrap.cli", "--help"], cwd=app, env=env, check=True, stdout=subprocess.DEVNULL)
        if len(wheels) == 3:
            compat_target = temp / "compat-install"
            compat_app = temp / "compat-application"
            compat_target.mkdir()
            compat_app.mkdir()
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "--no-deps", "--no-cache-dir", "--target", str(compat_target), str(wheels[0]), str(wheels[1]), str(wheels[2])],
                check=True,
            )
            compat_env = os.environ.copy()
            compat_env["PYTHONPATH"] = str(compat_target)
            compat_code = "import mdrap.models, models; assert models is mdrap.models; assert models.RawEvent is mdrap.models.RawEvent"
            subprocess.run([sys.executable, "-S", "-c", compat_code], cwd=compat_app, env=compat_env, check=True)
        print("Core-only isolation, paired core+contrib imports/CLI/plugins/collision, and opt-in compatibility checks passed.")


if __name__ == "__main__":
    main()
