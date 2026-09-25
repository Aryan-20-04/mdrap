from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from research import (
    ALLOWED_SEC_HOSTS,
    EdgarClient,
    EdgarError,
    SecurityError,
    check_xml_depth,
    parse_form4_xml,
    sanitize_output_text,
    sanitize_ticker,
    validate_url_security,
    validate_xml_security,
)


def test_xxe_entity_rejection():
    """Confirms that external entity references and DOCTYPE entity injections are blocked."""
    xxe_payload = b"""<?xml version="1.0" encoding="ISO-8859-1"?>
    <!DOCTYPE foo [
    <!ELEMENT foo ANY >
    <!ENTITY xxe SYSTEM "file:///etc/passwd" >]>
    <ownershipDocument>
        <rptOwnerName>&xxe;</rptOwnerName>
    </ownershipDocument>
    """
    with pytest.raises(SecurityError, match="Malicious XML detected"):
        validate_xml_security(xxe_payload)

    # Also test via parse_form4_xml
    with pytest.raises(SecurityError, match="Malicious XML detected"):
        parse_form4_xml(xxe_payload)


def test_billion_laughs_rejection():
    """Confirms that recursive Billion Laughs exponential entity expansion payloads are blocked."""
    billion_laughs = b"""<?xml version="1.0"?>
    <!DOCTYPE lolz [
     <!ENTITY lol "lol">
     <!ELEMENT lolz (#PCDATA)>
     <!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
     <!ENTITY lol2 "&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;">
     <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
    ]>
    <ownershipDocument>
     <rptOwnerName>&lol3;</rptOwnerName>
    </ownershipDocument>
    """
    with pytest.raises(SecurityError, match="Malicious XML detected"):
        validate_xml_security(billion_laughs)


def test_oversized_xml_rejection():
    """Confirms that XML payloads exceeding the maximum size ceiling are rejected prior to parsing."""
    large_payload = (
        b"<ownershipDocument>" + b"A" * (2 * 1024 * 1024 + 50) + b"</ownershipDocument>"
    )
    with pytest.raises(SecurityError, match="exceeds size ceiling"):
        validate_xml_security(large_payload, max_bytes=2 * 1024 * 1024)


def test_xml_depth_recursion_rejection():
    """Validates recursion depth limits preventing tag explosion DoS."""
    deep_xml = (
        "<root>"
        + ("<layer>" * 28)
        + "<item>Safe</item>"
        + ("</layer>" * 28)
        + "</root>"
    )
    root = ET.fromstring(deep_xml)
    with pytest.raises(SecurityError, match="nesting depth limit"):
        check_xml_depth(root, max_depth=20)


def test_ssrf_validation_blocks_unauthorized_destinations():
    """Verifies that requests can never be routed to internal IP addresses, AWS metadata, or non-SEC hosts."""
    unsafe_urls = [
        "http://data.sec.gov/submissions/CIK0000320193.json",  # plain HTTP
        "https://169.254.169.254/latest/meta-data/",  # AWS/cloud metadata
        "https://127.0.0.1:8080/admin",  # Loopback IP
        "https://localhost:443/data",  # Localhost name
        "https://internal-bank-api.prod/keys",  # Internal corporate host
        "https://evil-sec.gov/submissions/test.json",  # Lookalike spoof
        "https://data.sec.gov:8443/test.json",  # Non-standard port
        "https://data.sec.gov/submissions/../../etc/passwd",  # Path traversal sequence
    ]
    for url in unsafe_urls:
        with pytest.raises(SecurityError):
            validate_url_security(url)

    # Valid SEC URLs must pass without error
    valid_urls = [
        "https://data.sec.gov/submissions/CIK0000320193.json",
        "https://www.sec.gov/files/company_tickers.json",
        "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
    ]
    for url in valid_urls:
        validate_url_security(url)


def test_sanitize_output_text_masks_local_paths():
    """Confirms local file paths and developer usernames are scrubbed from CLI outputs."""
    raw_windows = r"Database initialized at C:\Users\KIIT0001\Desktop\Projects\mdrap\data\market.db with 500 events"
    scrubbed_win = sanitize_output_text(raw_windows)
    assert r"C:\Users\KIIT0001" not in scrubbed_win
    assert "[WORKSPACE]" in scrubbed_win

    raw_linux = (
        "Error reading archive at /home/runner/work/mdrap/data/archive/2026-09-12"
    )
    scrubbed_nix = sanitize_output_text(raw_linux)
    assert "/home/runner" not in scrubbed_nix
    assert "[WORKSPACE]" in scrubbed_nix

    raw_mac = "Cache written to /Users/testdev/Library/Caches/edgar.json"
    scrubbed_mac = sanitize_output_text(raw_mac)
    assert "/Users/testdev" not in scrubbed_mac
    assert "[WORKSPACE]" in scrubbed_mac

    clean_text = "Market data ticker AAPL: 50 trades, 120 quotes processed."
    assert sanitize_output_text(clean_text) == clean_text


def test_submissions_caching_speed_and_ttl(tmp_path):
    """Verifies that submissions responses are cached in memory and disk, honoring TTL and fresh flag."""
    client = EdgarClient(cache_dir=tmp_path, cache_ttl=10.0)

    mock_submissions = {
        "cik": "0000320193",
        "entityType": "operating",
        "sic": "3571",
        "sicDescription": "Electronic Computers",
        "name": "Apple Inc.",
        "tickers": ["AAPL"],
        "filings": {
            "recent": {
                "accessionNumber": ["0000320193-26-000100"],
                "filingDate": ["2026-09-01"],
                "reportDate": ["2026-08-31"],
                "form": ["8-K"],
                "primaryDocument": ["aapl-20260901.htm"],
                "primaryDocDescription": ["8-K"],
                "items": [["5.02"]],
            }
        },
    }

    with patch.object(client, "_request", return_value=mock_submissions) as mock_req:
        # First call: hits _request (cache miss)
        res1 = client._get_submissions("0000320193", fresh=False)
        assert res1["name"] == "Apple Inc."
        assert mock_req.call_count == 1

        # Second call: served from memory cache (zero network calls)
        res2 = client._get_submissions("0000320193", fresh=False)
        assert res2["name"] == "Apple Inc."
        assert mock_req.call_count == 1

        # Clear memory cache: served from disk cache
        client._submissions_mem_cache.clear()
        res3 = client._get_submissions("0000320193", fresh=False)
        assert res3["name"] == "Apple Inc."
        assert mock_req.call_count == 1

        # Forced fresh call: bypasses cache and calls _request again
        res4 = client._get_submissions("0000320193", fresh=True)
        assert res4["name"] == "Apple Inc."
        assert mock_req.call_count == 2


def test_cik_lookup_caching(tmp_path):
    """Verifies that CIK lookups are cached in memory for O(1) performance."""
    client = EdgarClient(cache_dir=tmp_path)
    client._ticker_map = {
        "AAPL": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        "MSFT": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
    }

    cik1 = client.lookup_cik("AAPL")
    assert cik1 == "0000320193"
    assert "AAPL" in client._cik_cache

    cik2 = client.lookup_cik("AAPL")
    assert cik2 == "0000320193"
