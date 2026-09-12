from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

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
    assert sanitize_ticker('aapl') == 'AAPL'
    assert sanitize_ticker('brk.a') == 'BRK.A'
    assert sanitize_ticker('btc-usd') == 'BTC-USD'
    assert sanitize_ticker('nvda_1') == 'NVDA_1'


@pytest.mark.parametrize('bad_ticker', [
    '',
    '   ',
    '../AAPL',
    'AAPL/TSLA',
    'AAPL\\TSLA',
    'AAPL; DROP TABLE',
    'A' * 15,
    123,
    None,
])
def test_sanitize_ticker_invalid(bad_ticker):
    with pytest.raises(SecurityError):
        sanitize_ticker(bad_ticker)


def test_token_bucket_pacer():
    pacer = TokenBucketPacer(rate=100.0, capacity=2.0)
    pacer.acquire()
    assert pacer.tokens <= 1.0


def test_edgar_client_rejects_http():
    client = EdgarClient()
    with pytest.raises(SecurityError, match='only HTTPS is permitted'):
        client._request('http://data.sec.gov/test')


def test_edgar_client_memory_ceiling():
    client = EdgarClient()
    mock_resp = MagicMock()
    mock_resp.headers.get.return_value = '20000000'  # 20 MB (> 15 MB cap)
    mock_resp.__enter__.return_value = mock_resp

    with patch('urllib.request.urlopen', return_value=mock_resp):
        with pytest.raises(SecurityError, match='Response exceeds memory ceiling'):
            client._request('https://data.sec.gov/test')


def test_profile_and_events_parsing(tmp_path):
    mock_submissions = {
        'cik': '0000320193',
        'name': 'Apple Inc.',
        'sic': '3571',
        'sicDescription': 'ELECTRONIC COMPUTERS',
        'stateOfIncorporation': 'CA',
        'fiscalYearEnd': '0930',
        'filings': {
            'recent': {
                'accessionNumber': ['0000320193-24-000001', '0000320193-24-000002'],
                'form': ['8-K', '10-Q'],
                'filingDate': ['2024-02-01', '2024-01-15'],
                'reportDate': ['2024-02-01', '2024-01-15'],
                'primaryDocument': ['event.htm', 'quarter.htm'],
                'primaryDocDescription': ['Material Event: Earnings Release', 'Quarterly Report'],
                'items': ['2.02,7.01', ''],
            }
        }
    }

    client = EdgarClient(cache_dir=tmp_path)
    client._ticker_map = {'AAPL': {'cik_str': 320193, 'ticker': 'AAPL', 'title': 'Apple Inc.'}}

    with patch.object(client, '_request', return_value=mock_submissions):
        prof = client.get_profile('AAPL')
        assert prof.ticker == 'AAPL'
        assert prof.cik == '0000320193'
        assert prof.name == 'Apple Inc.'
        assert prof.sic == '3571'

        events = client.get_material_events('AAPL')
        assert len(events) == 1
        assert events[0].form == '8-K'
        assert events[0].items == ['2.02', '7.01']
        assert 'Archives/edgar/data/320193' in events[0].filing_url


def test_insiders_and_facts_parsing(tmp_path):
    mock_submissions = {
        'filings': {
            'recent': {
                'accessionNumber': ['0000320193-24-000010'],
                'form': ['4'],
                'filingDate': ['2024-03-01'],
                'reportDate': ['2024-02-28'],
                'primaryDocument': ['form4.xml'],
                'primaryDocDescription': ['Statement of Changes in Beneficial Ownership'],
            }
        }
    }

    mock_facts = {
        'facts': {
            'us-gaap': {
                'Revenues': {
                    'units': {
                        'USD': [
                            {'end': '2023-09-30', 'val': 383285000000, 'form': '10-K', 'filed': '2023-11-03'}
                        ]
                    }
                }
            }
        }
    }

    client = EdgarClient(cache_dir=tmp_path)
    client._ticker_map = {'AAPL': {'cik_str': 320193, 'ticker': 'AAPL', 'title': 'Apple Inc.'}}

    with patch.object(client, '_request', side_effect=[mock_submissions, mock_facts]):
        insiders = client.get_insiders('AAPL')
        assert len(insiders) == 1
        assert insiders[0].form == '4'

        facts = client.get_company_facts('AAPL', metric='Revenues')
        assert len(facts) == 1
        assert facts[0]['value'] == 383285000000
