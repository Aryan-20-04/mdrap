"""
SEC EDGAR Corporate Filings & Regulatory Intelligence Pipeline.

This module provides institutional-grade ingestion and extraction for SEC EDGAR corporate filings:
- Form 8-K Item Taxonomy: Decodes material triggers (Item 1.01 Definitive Agreements,
  2.02 Financial Results, 5.02 Officer/Director changes) with urgency classification.
- Form 4 Insider Transaction Parser: Parses XML filings to isolate insider open-market purchases,
  dispositions, option grants, and officer/director ownership changes.
- Automated SEC CIK Lookups: Dynamically resolves company tickers to 10-digit zero-padded CIKs
  with local TTL caching and token-bucket rate limiting (<10 requests/sec).
- Air-Gapped Security Protections: Validates allowed SEC hostnames, enforces strict SSL
  certificate verification, and scrubs workspace user paths from telemetry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import gzip
import json
import os
from pathlib import Path
import re
import ssl
import time
from typing import Any
import urllib.request
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

TICKER_REGEX = re.compile(r"^[A-Za-z0-9.\-_]{1,10}$")
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
MAX_RESPONSE_BYTES = 15 * 1024 * 1024
MAX_XML_BYTES = 2 * 1024 * 1024  # 2 MB limit for Form 4 XML
ALLOWED_SEC_HOSTS = frozenset({"data.sec.gov", "www.sec.gov", "sec.gov"})
DEFAULT_USER_AGENT = "MDRAP-MarketPlatform/1.0 (Institutional Market Research; Contact: market-data-desk@mdrap.org)"
USER_PATH_PATTERN = re.compile(r"[a-zA-Z]:\\(?:Users|home)\\[^\s\\/]+", re.IGNORECASE)
POSIX_USER_PATH_PATTERN = re.compile(r"/(?:home|Users)/[^\s/]+")

COMMON_CIK_CACHE: dict[str, str] = {
    "AAPL": "0000320193",
    "MSFT": "0000789019",
    "NVDA": "0001045810",
    "AMZN": "0001018724",
    "GOOGL": "0001652044",
    "GOOG": "0001652044",
    "META": "0001326801",
    "TSLA": "0001318605",
    "JPM": "0000019617",
    "V": "0001403161",
    "XOM": "0000034088",
    "WMT": "0000104169",
}


class SecurityError(Exception):
    pass


class EdgarError(Exception):
    pass


def sanitize_output_text(text: str) -> str:
    """Scrubs local file system user paths and sensitive environment details to protect non-public data."""
    if not isinstance(text, str):
        return text
    scrubbed = USER_PATH_PATTERN.sub("[WORKSPACE]", text)
    return POSIX_USER_PATH_PATTERN.sub("[WORKSPACE]", scrubbed)


def validate_url_security(url: str) -> None:
    """Strict SSRF mitigation: ensures requests only route to allowlisted SEC EDGAR HTTPS hosts."""
    if not isinstance(url, str) or not url.startswith("https://"):
        raise SecurityError(
            "Insecure transport: only HTTPS is permitted for SEC requests"
        )

    parsed = urlparse(url)

    host = parsed.hostname
    if not host or host.lower() not in ALLOWED_SEC_HOSTS:
        raise SecurityError(
            f"Host not permitted: {host!r}. SEC requests restricted to official SEC hosts."
        )

    if parsed.port is not None and parsed.port != 443:
        raise SecurityError(f"Non-standard port not permitted: {parsed.port}")

    if ".." in parsed.path:
        raise SecurityError("Path traversal sequence detected in URL")


def validate_xml_security(xml_bytes: bytes, max_bytes: int = MAX_XML_BYTES) -> bytes:
    """Guards against XXE, Billion Laughs, and oversized XML payloads."""
    if not isinstance(xml_bytes, (bytes, bytearray)):
        raise SecurityError("XML payload must be bytes")

    if len(xml_bytes) > max_bytes:
        raise SecurityError(
            f"XML payload exceeds size ceiling of {max_bytes} bytes (got {len(xml_bytes)})"
        )

    upper = xml_bytes.upper()
    if b"<!ENTITY" in upper or b"SYSTEM " in upper or b"PUBLIC " in upper:
        raise SecurityError(
            "Malicious XML detected: entity declarations and external entities are strictly prohibited"
        )

    return bytes(xml_bytes)


def check_xml_depth(element, depth: int = 1, max_depth: int = 25) -> None:
    """Recursively validates that XML nesting depth does not exceed safety ceiling."""
    if depth > max_depth:
        raise SecurityError(f"XML document exceeds nesting depth limit of {max_depth}")
    for child in element:
        check_xml_depth(child, depth + 1, max_depth)


def sanitize_ticker(ticker: str) -> str:
    if not isinstance(ticker, str):
        raise SecurityError("Ticker must be a string")
    cleaned = ticker.strip().upper()
    if (
        not cleaned
        or not TICKER_REGEX.match(cleaned)
        or "/" in cleaned
        or "\\" in cleaned
        or ".." in cleaned
    ):
        raise SecurityError(f"Invalid or unsafe ticker symbol: {ticker!r}")
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


# Official SEC Form 8-K Item Taxonomy: code -> (readable_title, category, emoji)
ITEM_TAXONOMY: dict[str, tuple[str, str, str]] = {
    "1.01": ("Entry into a Material Definitive Agreement", "Deals & Contracts", "🤝"),
    "1.02": ("Termination of a Material Definitive Agreement", "Contracts", "⚠️"),
    "1.03": ("Bankruptcy or Receivership", "Solvency Risk", "☠️"),
    "1.04": ("Mine Safety - Reporting of Violations", "Operations", "⚠️"),
    "1.05": ("Material Cybersecurity Incident", "Cyber Risk", "🛡️"),
    "2.01": ("Completion of Acquisition or Disposition of Assets", "Deals & M&A", "🏢"),
    "2.02": (
        "Results of Operations & Financial Condition (Earnings)",
        "Financials",
        "📊",
    ),
    "2.03": (
        "Creation of Direct Financial Obligation / Debt",
        "Debt & Financing",
        "💳",
    ),
    "2.04": (
        "Acceleration of Direct Financial Obligation / Default",
        "Credit Risk",
        "🚨",
    ),
    "2.05": (
        "Costs Associated with Exit / Restructuring / Layoffs",
        "Restructuring",
        "✂️",
    ),
    "2.06": ("Material Impairments / Asset Write-Down", "Asset Write-down", "📉"),
    "3.01": (
        "Notice of Delisting / Non-Compliance with Listing Rules",
        "Listing Risk",
        "🚫",
    ),
    "3.02": ("Unregistered Sales of Equity Securities", "Dilution", "💵"),
    "3.03": (
        "Material Modification to Rights of Security Holders",
        "Shareholder Rights",
        "⚖️",
    ),
    "4.01": ("Changes in Registrant's Certifying Accountant", "Auditor Change", "🔍"),
    "4.02": (
        "Non-Reliance on Financials / Accounting Restatement",
        "Restatement Risk",
        "🚨",
    ),
    "5.01": ("Changes in Control of Registrant", "Takeover / Control", "👑"),
    "5.02": (
        "Departure / Election of Directors or Principal Officers",
        "Executive Leadership",
        "👤",
    ),
    "5.03": (
        "Amendments to Articles of Incorporation or Bylaws",
        "Corporate Governance",
        "📜",
    ),
    "5.04": (
        "Temporary Suspension of Trading Under Benefit Plans",
        "Trading Lockup",
        "🔒",
    ),
    "5.05": ("Amendments / Waiver to Code of Ethics", "Ethics", "⚖️"),
    "5.06": ("Change in Shell Company Status", "Corporate Structure", "🐚"),
    "5.07": (
        "Submission of Matters to a Vote of Security Holders",
        "Shareholder Vote",
        "🗳️",
    ),
    "5.08": ("Shareholder Director Nominations", "Proxy / Nomination", "🗳️"),
    "7.01": ("Regulation FD Disclosure", "Public Disclosure", "📢"),
    "8.01": ("Other Material Events", "Other Events", "📝"),
    "9.01": ("Financial Statements and Exhibits", "Exhibits", "📎"),
}


def decode_8k_items(items: list[str]) -> tuple[list[str], str, str]:
    """Decodes a list of 8-K item trigger numbers into readable headlines, category, and urgency."""
    decoded = []
    category = "General"
    urgency = "INFORMATIONAL"

    critical_items = {"1.03", "4.02", "1.05", "2.04", "3.01"}
    high_items = {"1.01", "2.01", "2.02", "2.05", "4.01", "5.01", "5.02"}
    medium_items = {"1.02", "2.03", "2.06", "3.02", "3.03", "5.03", "7.01"}

    for item in items:
        clean_item = item.strip()
        if clean_item in ITEM_TAXONOMY:
            title, cat, emoji = ITEM_TAXONOMY[clean_item]
            decoded.append(f"{emoji} {clean_item}: {title}")
            if category == "General" and clean_item != "9.01":
                category = cat
        else:
            decoded.append(f"Item {clean_item}")

    item_set = {item.strip() for item in items}
    if item_set & critical_items:
        urgency = "CRITICAL"
    elif item_set & high_items:
        urgency = "HIGH"
    elif item_set & medium_items:
        urgency = "MEDIUM"

    return decoded, category, urgency


@dataclass
class MaterialEvent:
    ticker: str
    accession_number: str
    form: str
    filing_date: str
    report_date: str
    items: list[str]
    description: str
    filing_url: str
    decoded_items: list[str] = field(default_factory=list)
    primary_category: str = "General"
    urgency: str = "INFORMATIONAL"


@dataclass
class InsiderTrade:
    ticker: str
    accession_number: str
    filing_date: str
    transaction_date: str
    owner_name: str
    officer_title: str
    is_director: bool
    is_officer: bool
    is_ten_percent_owner: bool
    transaction_code: str
    action: str
    shares: float
    price_per_share: float
    total_value: float
    shares_owned_after: float
    security_title: str
    direct_or_indirect: str
    filing_url: str


def parse_form4_xml(
    xml_content: str | bytes,
    ticker: str = "",
    accession_number: str = "",
    filing_date: str = "",
    filing_url: str = "",
) -> list[InsiderTrade]:
    """Parses standard SEC Form 4 XML into structured InsiderTrade instances."""
    trades: list[InsiderTrade] = []
    if not xml_content:
        return trades

    if isinstance(xml_content, str):
        xml_bytes = xml_content.encode("utf-8")
    else:
        xml_bytes = xml_content

    # Strict security validation (XXE, Billion Laughs entity expansion, size ceiling)
    xml_bytes = validate_xml_security(xml_bytes)

    try:
        root = ET.fromstring(xml_bytes)
    except Exception as exc:
        if isinstance(exc, SecurityError):
            raise
        return trades

    # Validate nesting depth against XML recursion DoS attacks
    check_xml_depth(root)

    # Reporting Owner Info
    owner_name = (root.findtext(".//rptOwnerName") or "Unknown Owner").strip()
    owner_title = (root.findtext(".//officerTitle") or "").strip()
    is_dir_text = (root.findtext(".//isDirector") or "").strip().lower()
    is_off_text = (root.findtext(".//isOfficer") or "").strip().lower()
    is_ten_text = (root.findtext(".//isTenPercentOwner") or "").strip().lower()

    is_director = is_dir_text in ("1", "true")
    is_officer = is_off_text in ("1", "true")
    is_ten_percent = is_ten_text in ("1", "true")

    if not owner_title:
        roles = []
        if is_director:
            roles.append("Director")
        if is_ten_percent:
            roles.append("10% Owner")
        owner_title = ", ".join(roles) if roles else "Insider"

    tx_nodes = root.findall(".//nonDerivativeTransaction")
    is_deriv = False
    if not tx_nodes:
        tx_nodes = root.findall(".//derivativeTransaction")
        is_deriv = True

    for tx in tx_nodes:
        default_title = "Derivative / Option" if is_deriv else "Common Stock"
        sec_title = (tx.findtext(".//securityTitle/value") or default_title).strip()
        tx_date = tx.findtext(".//transactionDate/value") or filing_date
        code = (tx.findtext(".//transactionCode") or "").strip().upper()
        acq_disp = (
            (tx.findtext(".//transactionAcquiredDisposedCode/value") or "")
            .strip()
            .upper()
        )

        def _val(path: str) -> float:
            try:
                return float(tx.findtext(path) or 0.0)
            except ValueError:
                return 0.0

        shares = _val(".//transactionShares/value")
        price = _val(".//transactionPricePerShare/value")
        owned_after = _val(".//sharesOwnedFollowingTransaction/value")
        direct = (
            (tx.findtext(".//directOrIndirectOwnership/value") or "D").strip().upper()
        )

        if is_deriv:
            action = "EXERCISE" if code == "M" else (code or "DERIVATIVE")
        elif code == "P" or (acq_disp == "A" and code in ("P", "I")):
            action = "BUY"
        elif code == "S" or (acq_disp == "D" and code == "S"):
            action = "SELL"
        elif code == "A":
            action = "GRANT"
        elif code == "M":
            action = "EXERCISE"
        elif code == "F":
            action = "TAX"
        elif code == "G":
            action = "GIFT"
        elif acq_disp == "A":
            action = f"ACQUIRE ({code})" if code else "ACQUIRE"
        elif acq_disp == "D":
            action = f"DISPOSE ({code})" if code else "DISPOSE"
        else:
            action = code or "OTHER"

        trades.append(
            InsiderTrade(
                ticker=ticker,
                accession_number=accession_number,
                filing_date=filing_date,
                transaction_date=tx_date,
                owner_name=owner_name.strip(),
                officer_title=owner_title.strip(),
                is_director=is_director,
                is_officer=is_officer,
                is_ten_percent_owner=is_ten_percent,
                transaction_code=code,
                action=action,
                shares=shares,
                price_per_share=price,
                total_value=shares * price,
                shares_owned_after=owned_after,
                security_title=sec_title,
                direct_or_indirect=direct,
                filing_url=filing_url,
            )
        )

    return trades


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
        cache_dir: str | Path = "data/edgar_cache",
        user_agent: str | None = None,
        timeout: float = 15.0,
        rate_limit: float = 9.0,
        cache_ttl: float = 300.0,
    ):
        self.cache_dir = Path(cache_dir)
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self.pacer = TokenBucketPacer(rate=rate_limit, capacity=rate_limit)
        self.user_agent = user_agent or os.environ.get(
            "MDRAP_EDGAR_USER_AGENT",
            DEFAULT_USER_AGENT,
        )
        self._ticker_map: dict[str, dict[str, Any]] | None = None
        self._cik_cache: dict[str, str] = dict(COMMON_CIK_CACHE)
        self._submissions_mem_cache: dict[str, tuple[dict[str, Any], float]] = {}
        self._facts_mem_cache: dict[str, tuple[dict[str, Any], float]] = {}
        self._ssl_context = self._build_ssl_context()

    def _build_ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx

    def _request_bytes(self, url: str) -> bytes:
        validate_url_security(url)
        self.pacer.acquire()

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept-Encoding": "gzip, deflate",
                "Host": "data.sec.gov" if "data.sec.gov" in url else "www.sec.gov",
                "Accept": "*/*",
            },
            method="GET",
        )

        try:
            with urllib.request.urlopen(
                req, timeout=self.timeout, context=self._ssl_context
            ) as response:
                content_len = response.headers.get("Content-Length")
                if content_len:
                    try:
                        if int(content_len.strip()) > MAX_RESPONSE_BYTES:
                            raise SecurityError(
                                f"Response exceeds memory ceiling of {MAX_RESPONSE_BYTES} bytes"
                            )
                    except ValueError:
                        pass

                raw_bytes = bytearray()
                while chunk := response.read(64 * 1024):
                    raw_bytes.extend(chunk)
                    if len(raw_bytes) > MAX_RESPONSE_BYTES:
                        raise SecurityError(
                            f"Response stream exceeded memory cap of {MAX_RESPONSE_BYTES} bytes"
                        )

                try:
                    return gzip.decompress(raw_bytes)
                except Exception:
                    return bytes(raw_bytes)
        except HTTPError as exc:
            if exc.code == 404:
                raise EdgarError(f"SEC resource not found (HTTP 404): {url}") from None
            if exc.code == 429:
                raise EdgarError(
                    "SEC EDGAR rate limit hit (HTTP 429); retry after backoff"
                ) from None
            raise EdgarError(f"SEC EDGAR request failed (HTTP {exc.code})") from None
        except (URLError, TimeoutError, ConnectionError) as exc:
            raise EdgarError(
                f"Network failure connecting to SEC EDGAR: {exc}"
            ) from None

    def _request(self, url: str) -> dict:
        raw = self._request_bytes(url)
        try:
            return json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise EdgarError(f"Failed to parse SEC JSON response: {exc}") from None

    def _request_text(self, url: str) -> str:
        raw = self._request_bytes(url)
        try:
            return raw.decode("utf-8", errors="replace")
        except Exception as exc:
            raise EdgarError(f"Failed to decode SEC text response: {exc}") from None

    def _ensure_ticker_map(self) -> dict[str, dict[str, Any]]:
        if self._ticker_map is not None:
            return self._ticker_map

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = self.cache_dir / "tickers.json"

        if cache_file.exists():
            try:
                if time.time() - cache_file.stat().st_mtime < 7 * 86400:
                    raw_data = json.loads(cache_file.read_text(encoding="utf-8"))
                    self._ticker_map = {
                        item["ticker"].upper(): item for item in raw_data.values()
                    }
                    return self._ticker_map
            except Exception:
                pass

        raw_data = self._request(SEC_TICKERS_URL)
        temp_file = self.cache_dir / f".tickers_{os.getpid()}.tmp"
        temp_file.write_text(json.dumps(raw_data), encoding="utf-8")
        os.replace(temp_file, cache_file)

        self._ticker_map = {item["ticker"].upper(): item for item in raw_data.values()}
        return self._ticker_map

    def _get_cached_sec_json(
        self, subfolder: str, cik: str, url_template: str, fresh: bool = False
    ) -> dict:
        cik_clean = str(int(cik)).zfill(10)
        prefix = "sub" if subfolder == "submissions" else "facts"
        cache_key = f"{prefix}_{cik_clean}"
        mem_cache = (
            self._submissions_mem_cache
            if subfolder == "submissions"
            else self._facts_mem_cache
        )
        now = time.time()

        if not fresh and cache_key in mem_cache:
            data, exp = mem_cache[cache_key]
            if now < exp:
                return data

        cache_sub_dir = self.cache_dir / subfolder
        cache_sub_dir.mkdir(parents=True, exist_ok=True)
        disk_file = cache_sub_dir / f"CIK{cik_clean}.json"

        if not fresh and disk_file.exists():
            try:
                if now - disk_file.stat().st_mtime < self.cache_ttl:
                    data = json.loads(disk_file.read_bytes())
                    mem_cache[cache_key] = (data, now + self.cache_ttl)
                    return data
            except Exception:
                pass

        data = self._request(url_template.format(cik=cik_clean))
        try:
            disk_file.write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            pass

        mem_cache[cache_key] = (data, now + self.cache_ttl)
        return data

    def _get_submissions(self, cik: str, fresh: bool = False) -> dict:
        """Fetches submissions JSON with in-memory & disk caching (300s TTL) for maximum speed."""
        return self._get_cached_sec_json(
            "submissions", cik, SEC_SUBMISSIONS_URL, fresh=fresh
        )

    def _get_company_facts(self, cik: str, fresh: bool = False) -> dict:
        """Fetches company facts JSON with in-memory & disk caching (300s TTL) for maximum speed."""
        return self._get_cached_sec_json("facts", cik, SEC_FACTS_URL, fresh=fresh)

    def lookup_cik(self, ticker: str) -> str:
        clean_ticker = sanitize_ticker(ticker)
        if clean_ticker in self._cik_cache:
            return self._cik_cache[clean_ticker]
        mapping = self._ensure_ticker_map()
        info = mapping.get(clean_ticker)
        if not info:
            raise EdgarError(
                f"Ticker {clean_ticker!r} not found in SEC company registry"
            )
        cik = str(info["cik_str"]).zfill(10)
        self._cik_cache[clean_ticker] = cik
        return cik

    def get_profile(self, ticker: str, fresh: bool = False) -> CompanyProfile:
        clean_ticker = sanitize_ticker(ticker)
        cik = self.lookup_cik(clean_ticker)
        data = self._get_submissions(cik, fresh=fresh)

        recent = data.get("filings", {}).get("recent", {})
        total_recent = len(recent.get("accessionNumber", []))

        return CompanyProfile(
            ticker=clean_ticker,
            cik=cik,
            name=str(data.get("name", clean_ticker)),
            sic=str(data.get("sic", "N/A")),
            sic_description=str(data.get("sicDescription", "N/A")),
            state=str(data.get("stateOfIncorporation", "N/A")),
            fiscal_year_end=str(data.get("fiscalYearEnd", "N/A")),
            total_filings=total_recent,
        )

    def get_filings(
        self,
        ticker: str,
        form_type: str | None = None,
        limit: int = 20,
        fresh: bool = False,
    ) -> list[FilingRecord]:
        clean_ticker = sanitize_ticker(ticker)
        cik = self.lookup_cik(clean_ticker)
        data = self._get_submissions(cik, fresh=fresh)

        recent = data.get("filings", {}).get("recent", {})
        accessions = recent.get("accessionNumber", [])
        forms = recent.get("form", [])
        filing_dates = recent.get("filingDate", [])
        report_dates = recent.get("reportDate", [])
        primary_docs = recent.get("primaryDocument", [])
        descriptions = recent.get("primaryDocDescription", [])

        results = []
        target_form = form_type.strip().upper() if form_type else None

        for i in range(len(accessions)):
            f_form = forms[i] if i < len(forms) else ""
            if target_form and f_form.upper() != target_form:
                continue

            acc_raw = accessions[i]
            acc_nodash = acc_raw.replace("-", "")
            doc = primary_docs[i] if i < len(primary_docs) else ""
            filing_url = (
                f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}/{doc}"
                if doc
                else ""
            )

            results.append(
                FilingRecord(
                    ticker=clean_ticker,
                    accession_number=acc_raw,
                    form=f_form,
                    filing_date=filing_dates[i] if i < len(filing_dates) else "",
                    report_date=report_dates[i] if i < len(report_dates) else "",
                    primary_document=doc,
                    description=descriptions[i] if i < len(descriptions) else "",
                    filing_url=filing_url,
                )
            )
            if len(results) >= limit:
                break

        return results

    def get_material_events(
        self, ticker: str, limit: int = 15, fresh: bool = False
    ) -> list[MaterialEvent]:
        clean_ticker = sanitize_ticker(ticker)
        cik = self.lookup_cik(clean_ticker)
        data = self._get_submissions(cik, fresh=fresh)

        recent = data.get("filings", {}).get("recent", {})
        accessions = recent.get("accessionNumber", [])
        forms = recent.get("form", [])
        filing_dates = recent.get("filingDate", [])
        report_dates = recent.get("reportDate", [])
        primary_docs = recent.get("primaryDocument", [])
        descriptions = recent.get("primaryDocDescription", [])
        items_list = recent.get("items", [])

        events = []
        for i in range(len(accessions)):
            form = forms[i] if i < len(forms) else ""
            if form not in ("8-K", "8-K/A"):
                continue

            acc_raw = accessions[i]
            acc_nodash = acc_raw.replace("-", "")
            doc = primary_docs[i] if i < len(primary_docs) else ""
            filing_url = (
                f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}/{doc}"
                if doc
                else ""
            )

            raw_items = items_list[i] if i < len(items_list) else ""
            if isinstance(raw_items, (list, tuple)):
                items = [
                    str(it).strip(" []'\"")
                    for it in raw_items
                    if str(it).strip(" []'\"")
                ]
            else:
                items = [
                    it.strip(" []'\"")
                    for it in str(raw_items).split(",")
                    if it.strip(" []'\"")
                ]
            decoded_items, category, urgency = decode_8k_items(items)

            events.append(
                MaterialEvent(
                    ticker=clean_ticker,
                    accession_number=acc_raw,
                    form=form,
                    filing_date=filing_dates[i] if i < len(filing_dates) else "",
                    report_date=report_dates[i] if i < len(report_dates) else "",
                    items=items,
                    description=descriptions[i] if i < len(descriptions) else "",
                    filing_url=filing_url,
                    decoded_items=decoded_items,
                    primary_category=category,
                    urgency=urgency,
                )
            )
            if len(events) >= limit:
                break

        return events

    def get_insiders(
        self, ticker: str, limit: int = 20, fresh: bool = False
    ) -> list[FilingRecord]:
        clean_ticker = sanitize_ticker(ticker)
        return self.get_filings(clean_ticker, form_type="4", limit=limit, fresh=fresh)

    def get_insider_trades(
        self, ticker: str, limit: int = 10, fresh: bool = False
    ) -> list[InsiderTrade]:
        """Fetches and parses Form 4 insider transactions, with disk-cached XML."""
        clean_ticker = sanitize_ticker(ticker)
        cik = self.lookup_cik(clean_ticker)
        data = self._get_submissions(cik, fresh=fresh)

        recent = data.get("filings", {}).get("recent", {})
        accessions = recent.get("accessionNumber", [])
        forms = recent.get("form", [])
        filing_dates = recent.get("filingDate", [])
        primary_docs = recent.get("primaryDocument", [])

        xml_cache_dir = self.cache_dir / "xml"
        xml_cache_dir.mkdir(parents=True, exist_ok=True)

        all_trades: list[InsiderTrade] = []
        filings_processed = 0

        for i in range(len(accessions)):
            form = forms[i] if i < len(forms) else ""
            if form != "4":
                continue

            acc_raw = accessions[i]
            acc_nodash = acc_raw.replace("-", "")
            doc = primary_docs[i] if i < len(primary_docs) else ""
            f_date = filing_dates[i] if i < len(filing_dates) else ""
            filing_url = (
                f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}/{doc}"
                if doc
                else ""
            )

            cached_xml_file = xml_cache_dir / f"{acc_nodash}.xml"
            xml_bytes: bytes | None = None

            if cached_xml_file.exists():
                try:
                    xml_bytes = cached_xml_file.read_bytes()
                except Exception:
                    xml_bytes = None

            if xml_bytes is None:
                xml_filename = doc.split("/")[-1] if doc else "form4.xml"
                if not xml_filename.endswith(".xml"):
                    xml_filename = "form4.xml"
                doc_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}/{xml_filename}"
                try:
                    xml_bytes = self._request_bytes(doc_url)
                    cached_xml_file.write_bytes(xml_bytes)
                except Exception:
                    if xml_filename != "form4.xml":
                        try:
                            fallback_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}/form4.xml"
                            xml_bytes = self._request_bytes(fallback_url)
                            cached_xml_file.write_bytes(xml_bytes)
                        except Exception:
                            continue
                    else:
                        continue

            trades = parse_form4_xml(
                xml_bytes,
                ticker=clean_ticker,
                accession_number=acc_raw,
                filing_date=f_date,
                filing_url=filing_url,
            )
            all_trades.extend(trades)
            filings_processed += 1
            if filings_processed >= limit:
                break

        return all_trades

    def get_company_facts(
        self,
        ticker: str,
        metric: str = "Revenues",
        limit: int = 8,
        fresh: bool = False,
    ) -> list[dict[str, Any]]:
        clean_ticker = sanitize_ticker(ticker)
        cik = self.lookup_cik(clean_ticker)
        data = self._get_company_facts(cik, fresh=fresh)

        us_gaap = data.get("facts", {}).get("us-gaap", {})
        fact_node = us_gaap.get(metric)
        if not fact_node:
            for k, v in us_gaap.items():
                if k.lower() == metric.lower() or metric.lower() in k.lower():
                    fact_node = v
                    metric = k
                    break

        if not fact_node:
            available = list(us_gaap.keys())[:10]
            raise EdgarError(
                f"Metric {metric!r} not found for {clean_ticker}. Available samples: {available}"
            )

        units = fact_node.get("units", {})
        unit_key = next(iter(units.keys())) if units else "USD"
        rows = units.get(unit_key, [])

        filtered = [r for r in rows if r.get("form") in ("10-K", "10-Q") and "val" in r]
        filtered.sort(key=lambda x: str(x.get("end", "")), reverse=True)

        results = []
        for r in filtered[:limit]:
            results.append(
                {
                    "ticker": clean_ticker,
                    "metric": metric,
                    "form": r.get("form"),
                    "frame": r.get("frame", ""),
                    "end_date": r.get("end"),
                    "filed_date": r.get("filed"),
                    "value": r.get("val"),
                    "unit": unit_key,
                }
            )
        return results
