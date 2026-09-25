"""
Pytest configuration for MDRAP test suite.
"""

import os

# Enable demo keys during test runs
os.environ["MDRAP_DEMO"] = "1"
