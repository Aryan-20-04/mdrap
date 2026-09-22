# MDRAP Alternative Data & Research Flow Architecture

This document diagrams the execution lifecycle of the Alternative Data & SEC Research Engine (src/research.py).

---

## 1. High-Level System Flow

`
[ User CLI Command ]
  (e.g., mdrap edgar events AAPL, mdrap edgar insiders NVDA)
          │
          ▼
[ Input Sanitization Guard ]
  - Regex validation: ^[A-Z0-9.\-_]{1,10}$
  - Upper-case normalization
  - Path-traversal & injection rejection
          │
          ▼
[ Ticker-to-CIK Resolver ]
  - Check local cache: data/edgar_cache/tickers.json
  - Cache Hit (< 1ms): Return 10-digit CIK (e.g. AAPL -> 0000320193)
  - Cache Miss: Fetch from https://www.sec.gov/files/company_tickers.json
    └─ Atomic write to data/edgar_cache/tickers.json
          │
          ▼
[ Token-Bucket Rate Limiter ]
  - Rate: 9.0 tokens/sec (Capacity: 9.0)
  - Prevents bursting past SEC 10 req/s threshold
          │
          ▼
[ Hardened HTTPS Transport ]
  - TLS Verification: ssl.create_default_context()
  - User-Agent Compliance: MDRAP-Research/1.0
  - Response Size Guard: Cap at 10 MB (Memory-bomb protection)
  - Timeout: 15s hard ceiling
          │
          ▼
[ SEC EDGAR REST API ]
  ├─ Submissions: data.sec.gov/submissions/CIK{cik}.json
  └─ Company Facts: data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json
          │
          ▼
[ Zero-Copy Structural Parser ]
  ├─ Action: 'events'   -> Filter 8-K / 8-K/A entries & item codes
  ├─ Action: 'insiders' -> Filter Form 4 acquisitions & discards
  ├─ Action: 'filings'  -> Filter 10-K, 10-Q, 8-K, 4, S-1
  ├─ Action: 'profile'  -> Extract SIC, Name, State, Fiscal calendar
  └─ Action: 'facts'    -> Parse GAAP metric historical time series
          │
          ▼
[ Rich Terminal HUD / Table Renderer ]
  - Wall Street styled console table
  - Direct hyperlinks to official SEC filings
  - Sub-millisecond rendering
`

---

## 2. Security & Guardrails Detail

### A. Ticker Input Guard
`
Input String
     │
     ├── Regex Check: Match ^[A-Za-z0-9.\-_]{1,10}$ ?
     │      ├─ NO  ──> Raise ValueError('Invalid ticker format') [EXIT]
     │      └─ YES ──> Normalize: input.upper().strip()
`

### B. Rate Limiting Sequence
`
Thread/CLI Request
     │
     ▼
TokenBucketRateLimiter(9.0 eps)
     │
     ├── Current tokens >= 1.0 ?
     │      ├─ YES ──> Deduct token, proceed immediately
     │      └─ NO  ──> Sleep (1.0 - tokens) / rate, then proceed
`

### C. Streaming Memory Guard
`
HTTPS Response Socket
     │
     ▼
Read Buffer Loop (Chunk size: 64 KB)
     │
     ├── Total bytes read > 10,485,760 bytes (10 MB)?
     │      ├─ YES ──> Terminate socket immediately, raise SecurityError
     │      └─ NO  ──> Append to in-memory byte buffer
`

---

## 3. Data Schema & Models

### CompanyProfile
- 	icker: str
- cik: str (10-digit zero-padded)
- 
ame: str
- sic: str
- sic_description: str
- state: str
- iscal_year_end: str
- 	otal_filings: int

### MaterialEvent (Form 8-K)
- 	icker: str
- ccession_number: str
- iling_date: str (YYYY-MM-DD)
- 
eport_date: str (YYYY-MM-DD)
- orm: str ('8-K', '8-K/A')
- items: list[str] (e.g. ['2.02', '5.02'])
- description: str
- iling_url: str (direct sec.gov link)

### FilingRecord
- 	icker: str
- ccession_number: str
- orm: str ('10-K', '10-Q', '4', etc.)
- iling_date: str
- primary_document: str
- description: str
- iling_url: str
