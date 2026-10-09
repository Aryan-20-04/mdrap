# MDRAP Phase 9 — UAT Deployment & Teardown Runbook

## 1. Prerequisites & Environment Setup
- Python 3.10+ (Current: 3.13.1 x86_64)
- Set required UAT secrets:
  ```powershell
  $env:MDRAP_API_KEY_SALT = "uat_cluster_salt_phase9_secret_salt"
  $env:MDRAP_DAEMON_TOKEN = "uat_admin_token_phase9"
  ```

## 2. Automated Cluster Deployment Command
Execute the automated UAT deployment runner:
```powershell
python scripts/deploy_uat_cluster.py
```

### 2.1 Execution Steps Performed
1. Allocates isolated database files for Primary (`temp/_uat_pri.db`) and Secondary (`temp/_uat_sec.db`).
2. Spawns Primary `MarketDataDaemon` on loopback port.
3. Spawns Secondary `MarketDataDaemon` on separate loopback port.
4. Executes TCP health checks (`HEALTH` command) against both daemons.
5. Verifies storage path non-interference.
6. Emits structured report to `audit/phase9/deployment_validation_results.json`.

## 3. Graceful Shutdown & Workspace Teardown
1. Issue `QUIT` or call `daemon.stop()` on both runtime instances.
2. Verify all background writer threads join and file handles close.
3. Remove temporary database files and journal segments.
