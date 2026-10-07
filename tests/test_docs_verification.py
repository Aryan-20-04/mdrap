"""Documentation verification test suite (Phase 23).

Ensures documentation examples, API snippets, and relative file links
match the actual codebase state and execute without errors.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
import os
from urllib.parse import unquote

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
        target_path = _REPO_ROOT / unquote(clean_link)
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
    from _version import __version__

    assert mdrap.__version__ == __version__


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


def test_user_documentation_does_not_claim_unserved_ports_or_publish_fake_keys():
    """User-facing setup docs must not advertise the removed TCP port or a usable key."""
    user_docs = [
        (_REPO_ROOT / "README.md").read_text(encoding="utf-8"),
        (_REPO_ROOT / "docs" / "quickstart.md").read_text(encoding="utf-8"),
    ]
    combined = "\n".join(user_docs)
    assert "9001" not in combined
    assert not re.search(r"mdrap_live_[A-Za-z0-9_-]{20,}", combined)
    assert "not connected to a feed runtime" in user_docs[1]


def test_ci_and_test_configuration_do_not_force_demo_mode():
    """Security-sensitive tests must run under normal defaults in CI."""
    ci = (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    conftest = (_REPO_ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert "MDRAP_DEMO" not in ci
    assert "MDRAP_DEMO" not in conftest


def test_cli_version_matches_package_version_source():
    """The CLI and package must report the one version stored in _version.py."""
    from _version import __version__

    completed = subprocess.run(
        [sys.executable, "-m", "mdrap.cli", "version"],
        cwd=_REPO_ROOT,
        env={**os.environ, "PYTHONPATH": _SRC_DIR},
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == f"MDRAP v{__version__}"
    assert 'version = {attr = "mdrap._version.__version__"}' in (
        _REPO_ROOT / "pyproject.toml"
    ).read_text(encoding="utf-8")
    for module in ("api.py", "cli/__init__.py", "client.py", "alert_sinks.py"):
        assert "2.2.0" not in (_REPO_ROOT / "src" / "mdrap" / module).read_text(
            encoding="utf-8"
        )


def test_feed_registration_docs_describe_metadata_only_runtime():
    """The feed endpoint and docs must be explicit that registration starts no source."""
    api_source = (Path(_SRC_DIR) / "mdrap" / "api.py").read_text(encoding="utf-8")
    readme = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert '"status": "REGISTERED_NOT_RUNNING"' in api_source
    assert "no feed runtime is configured" in api_source
    assert "There is no supervisor that owns adapter lifecycle" in readme


def test_quickstart_canonical_engine_walkthrough(tmp_path):
    """Verify that documented quickstart canonical engine code executes without errors."""
    import tempfile
    from mdrap import Engine, EngineConfig, RawEvent

    wal_dir = str(tmp_path / "wal")
    engine = Engine.open(wal_dir, config=EngineConfig(db_path=":memory:"))

    raw = RawEvent(
        source="EXAMPLE",
        payload={
            "instrument": "AAPL",
            "event_type": "TRADE",
            "exchange_ts": 1_800_000_000.0,
            "sequence": 1,
            "price": 200.0,
            "quantity": 10.0,
        },
    )

    decision = engine.submit(raw)
    assert decision is not None
    canonical = decision.canonical_event
    assert canonical is not None
    assert canonical.instrument_id == "AAPL"
    assert canonical.price == 200.0

    ticks = engine.query("AAPL", limit=10)
    assert len(ticks) == 1
    assert ticks[0]["price"] == 200.0
    engine.close()

