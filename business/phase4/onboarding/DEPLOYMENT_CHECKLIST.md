# Pilot Deployment & Verification Checklist

Complete this checklist during Stage 2 of the onboarding workflow to verify that MDRAP is securely and correctly configured.

---

## 1. Host Provisioning & Permissions
- [ ] Host provisioned with minimum 2 vCPUs, 2 GB RAM, and high-speed SSD/NVMe storage.
- [ ] Dedicated unprivileged system user `mdrap` (UID 10001, GID 10001) created.
- [ ] Directory permissions on `/data` or `/var/lib/mdrap` restricted to user `mdrap` (`chmod 700`).
- [ ] Pre-flight script executed successfully:
  ```bash
  python deploy/validate_environment.py --data-dir /data
  ```

---

## 2. Configuration & Secrets Hardening
- [ ] Production configuration created from template: `cp deploy/config.example.toml deploy/config.toml`.
- [ ] High-entropy 32-byte cryptographic salt generated and set in `config.toml` or `MDRAP_API_KEY_SALT`:
  ```bash
  openssl rand -hex 32
  ```
- [ ] Verified that **zero** trading API secrets (e.g. `BINANCE_API_SECRET`, `COINBASE_API_SECRET`) exist in the environment or configuration.
- [ ] API host verified as loopback only (`host = "127.0.0.1"`).

---

## 3. Feed & Connector Verification
- [ ] Monitored feeds enabled in `config.toml` (e.g. Coinbase, Kraken, Binance).
- [ ] Public WebSocket endpoints reachable via outbound port 443.
- [ ] Quick smoke run executed:
  ```bash
  python -m mdrap.cli doctor
  ```

---

## 4. Container / Service Startup
- [ ] Launched via Docker Compose (`docker compose up -d`) or systemd (`sudo systemctl start mdrap.service`).
- [ ] Healthcheck probe verified:
  ```bash
  python deploy/healthcheck.py
  ```
  Returns exit code `0`.
- [ ] SQLite WAL mode verified active on database file (`PRAGMA journal_mode;` returns `wal`).

---

## 5. Sign-Off
- Deployment Engineer: ______________________________
- Date / Timestamp: ______________________________
- Status: `[ ] READY FOR PASSIVE SHADOW MONITORING`
