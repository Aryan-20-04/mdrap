import tomllib
import re
from pathlib import Path

root = Path(".")
with open(root / "pyproject.toml", "rb") as f:
    d = tomllib.load(f)
    print("pyproject.toml dynamic version attr:", d["tool"]["setuptools"]["dynamic"]["version"]["attr"])

with open(root / "compat" / "pyproject.toml", "rb") as f:
    d = tomllib.load(f)
    print("compat/pyproject.toml version:", d["project"]["version"])

with open(root / "contrib" / "pyproject.toml", "rb") as f:
    d = tomllib.load(f)
    print("contrib/pyproject.toml version:", d["project"]["version"])

with open(root / "sdk" / "rust" / "Cargo.toml", "r", encoding="utf-8") as f:
    m = re.search(r'version\s*=\s*"([^"]+)"', f.read())
    print("Cargo.toml version:", m.group(1) if m else "NOT FOUND")

import sys
sys.path.insert(0, str((root / "src").resolve()))
from mdrap._version import __version__ as src_ver
print("src/mdrap/_version.py __version__:", src_ver)

