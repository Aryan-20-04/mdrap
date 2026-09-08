import os
import glob
from setuptools import setup, find_packages

# Find all root-level python files in src/
src_files = glob.glob("src/*.py")
py_modules = list(set(["cli"] + [os.path.basename(f)[:-3] for f in src_files if not f.endswith("__init__.py")]))

setup(
    name="mdrap",
    version="1.0.0rc3",
    description="Market Data Reliability & Acceleration Platform",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    py_modules=py_modules,
    entry_points={
        "console_scripts": [
            "mdrap=cli:main",
        ],
    },
)
