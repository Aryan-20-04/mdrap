# Pre-Pilot Technical Discovery Questionnaire

Please complete this technical discovery form to tailor the MDRAP sidecar configuration for your infrastructure.

---

## 1. General Institution Information
- **Entity Name**:
- **Primary Technical Contact**:
- **Email**:
- **Title / Role**:
- **Date**:

---

## 2. Infrastructure & Deployment Environment
- **Target Host OS**: `[ ] Ubuntu / Debian Linux` | `[ ] RHEL / Rocky Linux` | `[ ] Windows Server` | `[ ] Other`
- **Deployment Mode**: `[ ] Docker / Compose Container` | `[ ] Linux systemd Service` | `[ ] Bare Metal`
- **Host Specifications**:
  - Available CPU Cores:
  - Available RAM (GB):
  - Storage Type (e.g. NVMe, SSD, Cloud EBS):
  - Free Disk Space (GB):
- **Network Topologies**:
  - Is outbound 443 (HTTPS/WSS) accessible directly, or via HTTP proxy?
  - Do you plan to colocate MDRAP on the same host as trading bots (SHM IPC), or over local loopback REST?

---

## 3. Market Data Providers & Target Feeds
- **Exchanges to Monitor**:
  - `[ ] Coinbase Exchange` (WebSocket)
  - `[ ] Kraken` (WebSocket)
  - `[ ] Binance Spot / Futures` (WebSocket)
  - `[ ] Databento / CME / Nasdaq` (SBE / DBN)
  - `[ ] Other Proprietary Market Feed`:
- **Target Instruments** (e.g. BTC-USD, ETH-USD, SOL-USD):
- **Estimated Aggregate Tick Throughput** (ticks/sec):

---

## 4. Current Pain Points & Pilot Goals
- **Primary Motivations for Evaluating MDRAP** (check all that apply):
  - `[ ] Catching burst sequence drops during high-volatility flash crashes`
  - `[ ] Quarantining crossed / inverted order books before strategy execution`
  - `[ ] Measuring venue clock skew and network jitter`
  - `[ ] Storing cryptographic provenance for compliance and trade dispute resolution`
  - `[ ] Deterministic replay for post-mortem algorithmic regression testing`
- **Success Criteria**: What concrete outcome will prove that MDRAP has delivered measurable value at the end of the 30-day pilot?
