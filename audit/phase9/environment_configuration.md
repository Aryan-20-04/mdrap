# MDRAP Phase 9 — UAT Environment Configuration Matrix

## 1. Configuration Parameters

| Parameter | UAT Value | Production Reference | Rationale |
|---|---|---|---|
| `host` | `127.0.0.1` | Dedicated NIC IP (e.g. `10.0.1.10`) | Strict loopback isolation during UAT. |
| `port` | Dynamic (`0` / Ephemeral) | Fixed (`9876`) | Avoid port collision across concurrent test instances. |
| `db_path` | `temp/_uat_<role>.db` | `/var/lib/mdrap/canonical.db` | Independent temp paths per daemon. |
| `use_live` | `False` | `True` | Zero connection to live exchange feeds during UAT. |
| `sim_speed_eps` | `0.0` (unthrottled) | Variable feed rate | Rapid testing of queue and pipeline capacity. |
| `MDRAP_API_KEY_SALT` | `uat_cluster_salt_phase9_secret_salt` | HSM / Vault Provisioned | Never committed to repo. |
| `MDRAP_DAEMON_TOKEN` | `uat_admin_token_phase9` | Vault Provisioned | Admin access authorization. |

## 2. Security & Anti-Production Collision Guards
1. **Zero Order Routing**: Order routing components in companion strategies are decoupled; no outbound FIX or broker API connections exist.
2. **Distinct Ports & IPC**: SHM names use `mdrap_uat_*` to avoid collision with any existing daemon instances.
