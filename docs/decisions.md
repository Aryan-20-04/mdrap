# Architecture Decision Record: Alternative Data & Company Research Engine

**Date:** 2026-09-12  
**Status:** Accepted  
**Scope:** Core Platform · Alternative Data Layer (src/research.py)

---

## 1. Context & Motivation

MDRAP has proven microsecond execution in processing raw exchange order book feeds (NASDAQ TotalView-ITCH 5.0, Binance, Polygon, Databento). However, quant trading and market microstructure analysis increasingly rely on **Alternative Data** and **Corporate Material Event Feeds** to explain sudden volatility spikes, liquidity voids, and spread widening.

Traders need to answer two fundamental questions without leaving the terminal:
1. *What major material event just happened to this company?* (CEO resigned, merger, lawsuit, cyber incident, bankruptcy, earnings).
2. *Are company insiders (C-suite, directors) putting their own money into open-market stock purchases?*

The goal of this module is to provide **pure, verifiable, structured data** with **zero predictions, zero AI hallucinations, zero web UI, and zero third-party framework overhead**.

---

## 2. Decision: SEC EDGAR Direct JSON API

### Alternatives Considered:
1. **HTML Web Scraping with BeautifulSoup/Selenium:**
   - *Rejected.* Extremely slow (seconds per page), fragile against UI layout changes, high memory footprint, violates MDRAP stdlib-first rule, and triggers aggressive IP rate bans.
2. **Commercial Market Data APIs (Polygon, AlphaVantage, Bloomberg, FMP):**
   - *Rejected.* Requires paid API keys, rate limits, external vendor lock-in, and introduces risk of credential leaks in open source code.
3. **Official US SEC EDGAR REST API (data.sec.gov):**
   - **Accepted.** Official, legally mandated US government repository for all public corporate filings.
   - 100% free, updated continuously in real-time, no API keys needed, and natively provides machine-readable JSON endpoints (submissions and companyfacts).

---

## 3. Scope of Data Provided

1. **Material Corporate Events (Form 8-K):**
   - Legally required within 4 business days of any unscheduled material corporate development.
   - Categorized by standard SEC item codes (e.g., Item 1.01 Material Agreements, Item 2.02 Results of Operations, Item 5.02 Departure of Directors/Key Officers).
2. **Insider Transactions (Form 4):**
   - Real-time tracking of executive and board member equity changes reported within 48 hours.
3. **Audited Financial Facts (XBRL):**
   - Machine-readable balance sheet and income statement time-series directly from GAAP disclosures (Revenue, Net Income, Operating Cash Flow, Total Debt).
4. **Company Profiles:**
   - Central Index Key (CIK), Standard Industrial Classification (SIC), state of incorporation, and fiscal year calendar.

---

## 4. Cyberattack Prevention & Security Architecture

To prevent attacks, abuse, and platform instability:

| Threat Vector | Mitigation Strategy | Implementation |
|---|---|---|
| **Denial of Service / SEC IP Ban** | SEC imposes a strict limit of 10 requests/second per IP. | In-process TokenBucketRateLimiter(rate=9.0, capacity=9.0) ensures outgoing requests never exceed 9 req/s. |
| **Command & Argument Injection** | Malicious ticker strings containing shell meta-characters or SQL injection. | Strict input sanitization using regex ^[A-Z0-9.\-_]{1,10}$. Rejects any invalid or traversal characters (.., /, \). |
| **Memory Bomb / Oversized Response** | Malicious DNS spoofing or proxy redirecting to an infinite data stream. | Hard response body limit of 10 MB. Streaming reads abort if Content-Length or actual read exceeds threshold. |
| **Man-in-the-Middle (MitM) / Eavesdropping** | Compromised networks tampering with filings or financial numbers. | Enforced HTTPS only with strict TLS certificate verification via ssl.create_default_context(). HTTP redirects are strictly rejected. |
| **Credential / Privacy Exposure** | Accidental leaking of private keys or corporate emails in logs/code. | The SEC requires a declared User-Agent (App/Version user@domain). MDRAP defaults to a public generic header (MDRAP-Research/1.0 admin@mdrap.internal) with optional environment override MDRAP_EDGAR_USER_AGENT. No passwords or secrets are ever recorded. |
| **Cache Poisoning** | Malicious local file tampering in cache directory. | Ticker mappings are stored in atomic local JSON files (data/edgar_cache/). Corrupted caches fall back to a fresh HTTPS pull. |

---

## 5. Performance & Architecture Constraints

- **Pure Python Standard Library:** Uses urllib.request, ssl, json, re, and dataclasses. Zero mandatory external dependencies.
- **Terminal First:** Formatted with rich console tables matching the rest of MDRAP. Zero web server or web page dependencies.
- **Target Response Time:** Sub-250ms for cached CIK queries; under 500ms for network-roundtrip filing queries.
