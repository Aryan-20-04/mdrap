from __future__ import annotations

from dataclasses import dataclass
import gzip
import json
import os
from pathlib import Path
import re
import ssl
import time
from typing import Any, Dict, List, Optional
import urllib.request
from urllib.error import HTTPError, URLError

TICKER_REGEX = re.compile(r'^[A-Za-z0-9.\-_]{1,10}$')
SEC_TICKERS_URL = 'https://www.sec.gov/files/company_tickers.json'
SEC_SUBMISSIONS_URL = 'https://data.sec.gov/submissions/CIK{cik}.json'
SEC_FACTS_URL = 'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json'
MAX_RESPONSE_BYTES = 15 * 1024 * 1024


class SecurityError(Exception):
    pass


class EdgarError(Exception):
    pass


def sanitize_ticker(ticker: str) -> str:
    if not isinstance(ticker, str):
        raise SecurityError('Ticker must be a string')
    cleaned = ticker.strip().upper()
    if not cleaned or not TICKER_REGEX.match(cleaned) or '/' in cleaned or '\\' in cleaned or '..' in cleaned:
        raise SecurityError(f'Invalid or unsafe ticker symbol: {ticker!r}')
    return cleaned


@dataclass
class CompanyProfile:
    ticker: str
    cik: str
    name: str
    sic: str
    sic_description: str
    state: str
    fiscal_year_end: str
    total_filings: int


@dataclass
class FilingRecord:
    ticker: str
    accession_number: str
    form: str
    filing_date: str
    report_date: str
    primary_document: str
    description: str
    filing_url: str


@dataclass
class MaterialEvent:
    ticker: str
    accession_number: str
    form: str
    filing_date: str
    report_date: str
    items: List[str]
    description: str
    filing_url: str


class TokenBucketPacer:
    def __init__(self, rate: float = 9.0, capacity: float = 9.0):
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last_update = time.monotonic()

    def acquire(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_update
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.last_update = now
        if self.tokens < 1.0:
            sleep_time = (1.0 - self.tokens) / self.rate
            time.sleep(sleep_time)
            self.tokens = 0.0
            self.last_update = time.monotonic()
        else:
            self.tokens -= 1.0


class EdgarClient:
    def __init__(
        self,
        cache_dir: str | Path = 'data/edgar_cache',
        user_agent: Optional[str] = None,
        timeout: float = 15.0,
        rate_limit: float = 9.0,
    ):
        self.cache_dir = Path(cache_dir)
        self.timeout = timeout
        self.pacer = TokenBucketPacer(rate=rate_limit, capacity=rate_limit)
        self.user_agent = user_agent or os.environ.get(
            'MDRAP_EDGAR_USER_AGENT',
            'MDRAP-Research/1.0 (contact: admin@mdrap.internal)',
        )
        self._ticker_map: Optional[Dict[str, dict]] = None

    def _build_ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx

    def _request(self, url: str) -> dict:
        if not url.startswith('https://'):
            raise SecurityError('Insecure transport: only HTTPS is permitted for SEC requests')
        
        self.pacer.acquire()

        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': self.user_agent,
                'Accept-Encoding': 'gzip, deflate',
                'Host': 'data.sec.gov' if 'data.sec.gov' in url else 'www.sec.gov',
                'Accept': 'application/json',
            },
            method='GET',
        )

        ctx = self._build_ssl_context()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=ctx) as response:
                content_len = response.headers.get('Content-Length')
                if content_len:
                    try:
                        if int(content_len.strip()) > MAX_RESPONSE_BYTES:
                            raise SecurityError(f'Response exceeds memory ceiling of {MAX_RESPONSE_BYTES} bytes')
                    except ValueError:
                        pass

                raw_bytes = bytearray()
                while chunk := response.read(64 * 1024):
                    raw_bytes.extend(chunk)
                    if len(raw_bytes) > MAX_RESPONSE_BYTES:
                        raise SecurityError(f'Response stream exceeded memory cap of {MAX_RESPONSE_BYTES} bytes')

                try:
                    data = gzip.decompress(raw_bytes)
                except Exception:
                    data = raw_bytes

                return json.loads(data.decode('utf-8'))
        except HTTPError as exc:
            if exc.code == 404:
                raise EdgarError(f'SEC resource not found (HTTP 404): {url}') from None
            if exc.code == 429:
                raise EdgarError('SEC EDGAR rate limit hit (HTTP 429); retry after backoff') from None
            raise EdgarError(f'SEC EDGAR request failed (HTTP {exc.code})') from None
        except (URLError, TimeoutError, ConnectionError) as exc:
            raise EdgarError(f'Network failure connecting to SEC EDGAR: {exc}') from None
        except (ValueError, UnicodeDecodeError) as exc:
            raise EdgarError(f'Failed to decode SEC JSON response: {exc}') from None

    def _ensure_ticker_map(self) -> Dict[str, dict]:
        if self._ticker_map is not None:
            return self._ticker_map

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = self.cache_dir / 'tickers.json'

        if cache_file.exists():
            try:
                if time.time() - cache_file.stat().st_mtime < 7 * 86400:
                    raw_data = json.loads(cache_file.read_text(encoding='utf-8'))
                    self._ticker_map = {item['ticker'].upper(): item for item in raw_data.values()}
                    return self._ticker_map
            except Exception:
                pass

        raw_data = self._request(SEC_TICKERS_URL)
        temp_file = self.cache_dir / f'.tickers_{os.getpid()}.tmp'
        temp_file.write_text(json.dumps(raw_data), encoding='utf-8')
        os.replace(temp_file, cache_file)

        self._ticker_map = {item['ticker'].upper(): item for item in raw_data.values()}
        return self._ticker_map

    def lookup_cik(self, ticker: str) -> str:
        clean_ticker = sanitize_ticker(ticker)
        mapping = self._ensure_ticker_map()
        info = mapping.get(clean_ticker)
        if not info:
            raise EdgarError(f'Ticker {clean_ticker!r} not found in SEC company registry')
        return str(info['cik_str']).zfill(10)

    def get_profile(self, ticker: str) -> CompanyProfile:
        clean_ticker = sanitize_ticker(ticker)
        cik = self.lookup_cik(clean_ticker)
        url = SEC_SUBMISSIONS_URL.format(cik=cik)
        data = self._request(url)

        recent = data.get('filings', {}).get('recent', {})
        total_recent = len(recent.get('accessionNumber', []))

        return CompanyProfile(
            ticker=clean_ticker,
            cik=cik,
            name=str(data.get('name', clean_ticker)),
            sic=str(data.get('sic', 'N/A')),
            sic_description=str(data.get('sicDescription', 'N/A')),
            state=str(data.get('stateOfIncorporation', 'N/A')),
            fiscal_year_end=str(data.get('fiscalYearEnd', 'N/A')),
            total_filings=total_recent,
        )

    def get_filings(
        self,
        ticker: str,
        form_type: Optional[str] = None,
        limit: int = 20,
    ) -> List[FilingRecord]:
        clean_ticker = sanitize_ticker(ticker)
        cik = self.lookup_cik(clean_ticker)
        url = SEC_SUBMISSIONS_URL.format(cik=cik)
        data = self._request(url)

        recent = data.get('filings', {}).get('recent', {})
        accessions = recent.get('accessionNumber', [])
        forms = recent.get('form', [])
        filing_dates = recent.get('filingDate', [])
        report_dates = recent.get('reportDate', [])
        primary_docs = recent.get('primaryDocument', [])
        descriptions = recent.get('primaryDocDescription', [])

        results = []
        target_form = form_type.strip().upper() if form_type else None

        for i in range(len(accessions)):
            f_form = forms[i] if i < len(forms) else ''
            if target_form and f_form.upper() != target_form:
                continue

            acc_raw = accessions[i]
            acc_nodash = acc_raw.replace('-', '')
            doc = primary_docs[i] if i < len(primary_docs) else ''
            filing_url = f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}/{doc}' if doc else ''

            results.append(
                FilingRecord(
                    ticker=clean_ticker,
                    accession_number=acc_raw,
                    form=f_form,
                    filing_date=filing_dates[i] if i < len(filing_dates) else '',
                    report_date=report_dates[i] if i < len(report_dates) else '',
                    primary_document=doc,
                    description=descriptions[i] if i < len(descriptions) else '',
                    filing_url=filing_url,
                )
            )
            if len(results) >= limit:
                break

        return results

    def get_material_events(self, ticker: str, limit: int = 15) -> List[MaterialEvent]:
        clean_ticker = sanitize_ticker(ticker)
        cik = self.lookup_cik(clean_ticker)
        url = SEC_SUBMISSIONS_URL.format(cik=cik)
        data = self._request(url)

        recent = data.get('filings', {}).get('recent', {})
        accessions = recent.get('accessionNumber', [])
        forms = recent.get('form', [])
        filing_dates = recent.get('filingDate', [])
        report_dates = recent.get('reportDate', [])
        primary_docs = recent.get('primaryDocument', [])
        descriptions = recent.get('primaryDocDescription', [])
        items_list = recent.get('items', [])

        events = []
        for i in range(len(accessions)):
            form = forms[i] if i < len(forms) else ''
            if form not in ('8-K', '8-K/A'):
                continue

            acc_raw = accessions[i]
            acc_nodash = acc_raw.replace('-', '')
            doc = primary_docs[i] if i < len(primary_docs) else ''
            filing_url = f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}/{doc}' if doc else ''
            
            raw_items = items_list[i] if i < len(items_list) else ''
            items = [item.strip() for item in str(raw_items).split(',') if item.strip()]

            events.append(
                MaterialEvent(
                    ticker=clean_ticker,
                    accession_number=acc_raw,
                    form=form,
                    filing_date=filing_dates[i] if i < len(filing_dates) else '',
                    report_date=report_dates[i] if i < len(report_dates) else '',
                    items=items,
                    description=descriptions[i] if i < len(descriptions) else '',
                    filing_url=filing_url,
                )
            )
            if len(events) >= limit:
                break

        return events

    def get_insiders(self, ticker: str, limit: int = 20) -> List[FilingRecord]:
        clean_ticker = sanitize_ticker(ticker)
        return self.get_filings(clean_ticker, form_type='4', limit=limit)

    def get_company_facts(self, ticker: str, metric: str = 'Revenues', limit: int = 8) -> List[dict]:
        clean_ticker = sanitize_ticker(ticker)
        cik = self.lookup_cik(clean_ticker)
        url = SEC_FACTS_URL.format(cik=cik)
        data = self._request(url)

        us_gaap = data.get('facts', {}).get('us-gaap', {})
        fact_node = us_gaap.get(metric)
        if not fact_node:
            for k, v in us_gaap.items():
                if k.lower() == metric.lower() or metric.lower() in k.lower():
                    fact_node = v
                    metric = k
                    break

        if not fact_node:
            available = list(us_gaap.keys())[:10]
            raise EdgarError(f'Metric {metric!r} not found for {clean_ticker}. Available samples: {available}')

        units = fact_node.get('units', {})
        unit_key = next(iter(units.keys())) if units else 'USD'
        rows = units.get(unit_key, [])

        filtered = [r for r in rows if r.get('form') in ('10-K', '10-Q') and 'val' in r]
        filtered.sort(key=lambda x: str(x.get('end', '')), reverse=True)

        results = []
        for r in filtered[:limit]:
            results.append({
                'ticker': clean_ticker,
                'metric': metric,
                'form': r.get('form'),
                'frame': r.get('frame', ''),
                'end_date': r.get('end'),
                'filed_date': r.get('filed'),
                'value': r.get('val'),
                'unit': unit_key,
            })
        return results
