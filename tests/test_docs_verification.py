"""Documentation verification test suite (Phase 23).

Ensures documentation examples, API snippets, and relative file links
match the actual codebase state and execute without errors.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC_DIR = str(_REPO_ROOT / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)


def test_readme_relative_links_exist():
    """Verify that all relative markdown links in README.md point to real files."""
    readme_path = _REPO_ROOT / "README.md"
    assert readme_path.exists(), "README.md not found"

    content = readme_path.read_text(encoding="utf-8")
    # Match markdown links: [text](path)
    links = re.findall(r"\[.*?\]\((?!http|https|#|mailto:)(.*?)\)", content)

    for link in links:
        # Strip anchor if present
        clean_link = link.split("#")[0]
        if not clean_link:
            continue
        target_path = _REPO_ROOT / clean_link
        assert target_path.exists(), (
            f"Broken relative link in README.md: {link} -> {target_path}"
        )


def test_public_sdk_imports_match_documentation():
    """Verify that documented SDK import patterns work without error."""
    # Documented pattern:
    # from mdrap import Client, MDRAPClient, MarketEvent
    import mdrap
    from mdrap import (
        CanonicalEvent,
        Client,
        EventType,
        MarketEvent,
        MDRAPClient,
        QualityStatus,
        Reason,
    )

    assert Client is not None
    assert MDRAPClient is not None
    assert MarketEvent is not None
    assert CanonicalEvent is not None
    assert EventType is not None
    assert QualityStatus is not None
    assert Reason is not None
    assert hasattr(mdrap, "__version__")
    assert mdrap.__version__ == "2.3.0"


def test_feed_adapter_documented_imports():
    """Verify that documented FeedAdapter protocol and reference implementation import cleanly."""
    from adapters import FeedAdapter, discover_adapters
    from adapters.reference import ReferenceFeedAdapter

    assert FeedAdapter is not None
    assert discover_adapters is not None

    adapter = ReferenceFeedAdapter("TEST_VENUE")
    adapter.connect()
    adapter.feed_simulated_packet(seq=1, price=150.0, qty=100.0)
    raw = adapter.receive()
    assert raw is not None
    canonical = adapter.normalize(raw)
    assert canonical.source == "TEST_VENUE"
    health = adapter.health()
    assert health["connected"] is True
    adapter.disconnect()
