"""
Pytest configuration for MDRAP test suite.
"""

import os

# Tests use a stable non-demo salt; production must configure its own value.
os.environ.setdefault("MDRAP_API_KEY_SALT", "test-only-mdrap-salt-not-for-deployment")
