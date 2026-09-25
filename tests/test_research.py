from __future__ import annotations
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from research import (
    CompanyProfile,
    EdgarClient,
    EdgarError,
    FilingRecord,
    MaterialEvent,
    SecurityError,
    TokenBucketPacer,
    sanitize_ticker,
)


def test_sanitize_ticker_valid():
    assert sanitize_ticker("aapl") == "AAPL"
    assert sanitize_ticker("brk.a") == "BRK.A"
    assert sanitize_ticker("btc-usd") == "BTC-USD"
    assert sanitize_ticker("nvda_1") == "NVDA_1"


@pytest.mark.parametrize(
    "bad_ticker",
    [
        "",
        "   ",
        "../AAPL",
        "AAPL/TSLA",
        "AAPL\\TSLA",
        "AAPL; DROP TABLE",
        "A" * 15,
        123,
        None,
    ],
)
def test_sanitize_ticker_invalid(bad_ticker):
    with pytest.raises(SecurityError):
        sanitize_ticker(bad_ticker)


def test_token_bucket_pacer():
    pacer = TokenBucketPacer(rate=100.0, capacity=2.0)
    pacer.acquire()
    assert pacer.tokens <= 1.0


def test_edgar_client_rejects_http():
    client = EdgarClient()
    with pytest.raises(SecurityError, match="only HTTPS is permitted"):
        client._request("http://data.sec.gov/test")


def test_edgar_client_memory_ceiling():
    client = EdgarClient()
    mock_resp = MagicMock()
    mock_resp.headers.get.return_value = "20000000"  # 20 MB (> 15 MB cap)
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        with pytest.raises(SecurityError, match="Response exceeds memory ceiling"):
            client._request("https://data.sec.gov/test")


def test_profile_and_events_parsing(tmp_path):
    mock_submissions = {
        "cik": "0000320193",
        "name": "Apple Inc.",
        "sic": "3571",
        "sicDescription": "ELECTRONIC COMPUTERS",
        "stateOfIncorporation": "CA",
        "fiscalYearEnd": "0930",
        "filings": {
            "recent": {
                "accessionNumber": ["0000320193-24-000001", "0000320193-24-000002"],
                "form": ["8-K", "10-Q"],
                "filingDate": ["2024-02-01", "2024-01-15"],
                "reportDate": ["2024-02-01", "2024-01-15"],
                "primaryDocument": ["event.htm", "quarter.htm"],
                "primaryDocDescription": [
                    "Material Event: Earnings Release",
                    "Quarterly Report",
                ],
                "items": ["2.02,7.01", ""],
            }
        },
    }

    client = EdgarClient(cache_dir=tmp_path)
    client._ticker_map = {
        "AAPL": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}
    }

    with patch.object(client, "_request", return_value=mock_submissions):
        prof = client.get_profile("AAPL")
        assert prof.ticker == "AAPL"
        assert prof.cik == "0000320193"
        assert prof.name == "Apple Inc."
        assert prof.sic == "3571"

        events = client.get_material_events("AAPL")
        assert len(events) == 1
        assert events[0].form == "8-K"
        assert events[0].items == ["2.02", "7.01"]
        assert "Archives/edgar/data/320193" in events[0].filing_url


def test_insiders_and_facts_parsing(tmp_path):
    mock_submissions = {
        "filings": {
            "recent": {
                "accessionNumber": ["0000320193-24-000010"],
                "form": ["4"],
                "filingDate": ["2024-03-01"],
                "reportDate": ["2024-02-28"],
                "primaryDocument": ["form4.xml"],
                "primaryDocDescription": [
                    "Statement of Changes in Beneficial Ownership"
                ],
            }
        }
    }

    mock_facts = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {
                                "end": "2023-09-30",
                                "val": 383285000000,
                                "form": "10-K",
                                "filed": "2023-11-03",
                            }
                        ]
                    }
                }
            }
        }
    }

    client = EdgarClient(cache_dir=tmp_path)
    client._ticker_map = {
        "AAPL": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}
    }

    with patch.object(client, "_request", side_effect=[mock_submissions, mock_facts]):
        insiders = client.get_insiders("AAPL")
        assert len(insiders) == 1
        assert insiders[0].form == "4"

        facts = client.get_company_facts("AAPL", metric="Revenues")
        assert len(facts) == 1
        assert facts[0]["value"] == 383285000000


def test_decode_8k_items():
    from research import decode_8k_items

    # Critical bankruptcy
    decoded, cat, urgency = decode_8k_items(["1.03"])
    assert urgency == "CRITICAL"
    assert cat == "Solvency Risk"
    assert "Bankruptcy" in decoded[0]

    # High executive change
    decoded, cat, urgency = decode_8k_items(["5.02", "9.01"])
    assert urgency == "HIGH"
    assert cat == "Executive Leadership"
    assert any("5.02" in d for d in decoded)
    assert any("9.01" in d for d in decoded)

    # Earnings
    decoded, cat, urgency = decode_8k_items(["2.02"])
    assert urgency == "HIGH"
    assert cat == "Financials"


def test_parse_form4_xml():
    from research import parse_form4_xml

    sample_xml = """<?xml version="1.0"?>
    <ownershipDocument>
        <periodOfReport>2026-09-08</periodOfReport>
        <reportingOwner>
            <reportingOwnerId>
                <rptOwnerName>Cook Timothy D</rptOwnerName>
            </reportingOwnerId>
            <reportingOwnerRelationship>
                <isDirector>true</isDirector>
                <isOfficer>true</isOfficer>
                <officerTitle>Chief Executive Officer</officerTitle>
            </reportingOwnerRelationship>
        </reportingOwner>
        <nonDerivativeTable>
            <nonDerivativeTransaction>
                <securityTitle><value>Common Stock</value></securityTitle>
                <transactionDate><value>2026-09-08</value></transactionDate>
                <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
                <transactionAmounts>
                    <transactionShares><value>50000</value></transactionShares>
                    <transactionPricePerShare><value>230.50</value></transactionPricePerShare>
                    <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
                </transactionAmounts>
                <postTransactionAmounts>
                    <sharesOwnedFollowingTransaction><value>3280000</value></sharesOwnedFollowingTransaction>
                </postTransactionAmounts>
                <ownershipNature>
                    <directOrIndirectOwnership><value>D</value></directOrIndirectOwnership>
                </ownershipNature>
            </nonDerivativeTransaction>
        </nonDerivativeTable>
    </ownershipDocument>
    """

    trades = parse_form4_xml(
        sample_xml,
        ticker="AAPL",
        accession_number="0000320193-26-000999",
        filing_date="2026-09-09",
        filing_url="https://sec.gov/test",
    )

    assert len(trades) == 1
    t = trades[0]
    assert t.ticker == "AAPL"
    assert t.owner_name == "Cook Timothy D"
    assert t.officer_title == "Chief Executive Officer"
    assert t.is_director is True
    assert t.is_officer is True
    assert t.action == "SELL"
    assert t.shares == 50000.0
    assert t.price_per_share == 230.50
    assert t.total_value == 50000.0 * 230.50
    assert t.shares_owned_after == 3280000.0
    assert t.direct_or_indirect == "D"


def test_get_insider_trades_cached(tmp_path):
    from research import EdgarClient

    mock_submissions = {
        "filings": {
            "recent": {
                "accessionNumber": ["0000320193-26-000888"],
                "form": ["4"],
                "filingDate": ["2026-09-05"],
                "primaryDocument": ["xslF345X06/form4.xml"],
            }
        }
    }

    sample_xml = b"""<?xml version="1.0"?>
    <ownershipDocument>
        <reportingOwner>
            <reportingOwnerId><rptOwnerName>Maestri Luca</rptOwnerName></reportingOwnerId>
            <reportingOwnerRelationship>
                <isOfficer>true</isOfficer>
                <officerTitle>CFO</officerTitle>
            </reportingOwnerRelationship>
        </reportingOwner>
        <nonDerivativeTable>
            <nonDerivativeTransaction>
                <securityTitle><value>Common Stock</value></securityTitle>
                <transactionDate><value>2026-09-04</value></transactionDate>
                <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
                <transactionAmounts>
                    <transactionShares><value>10000</value></transactionShares>
                    <transactionPricePerShare><value>225.00</value></transactionPricePerShare>
                    <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
                </transactionAmounts>
                <postTransactionAmounts>
                    <sharesOwnedFollowingTransaction><value>150000</value></sharesOwnedFollowingTransaction>
                </postTransactionAmounts>
            </nonDerivativeTransaction>
        </nonDerivativeTable>
    </ownershipDocument>
    """

    client = EdgarClient(cache_dir=tmp_path)
    client._ticker_map = {
        "AAPL": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}
    }

    # Pre-populate XML disk cache to test offline cache reading
    xml_cache = tmp_path / "xml"
    xml_cache.mkdir(parents=True, exist_ok=True)
    (xml_cache / "000032019326000888.xml").write_bytes(sample_xml)

    with patch.object(client, "_request", return_value=mock_submissions):
        trades = client.get_insider_trades("AAPL", limit=5)
        assert len(trades) == 1
        assert trades[0].owner_name == "Maestri Luca"
        assert trades[0].officer_title == "CFO"
        assert trades[0].action == "BUY"
        assert trades[0].shares == 10000.0
        assert trades[0].total_value == 2250000.0
