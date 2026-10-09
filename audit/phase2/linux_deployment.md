# MDRAP Phase 2 — Linux Deployment & Security Hardening Specification

**Document Identifier**: `MDRAP-DEPLOY-P2-001`  
**Status**: APPROVED & IMPLEMENTED  
**Author**: Principal Systems Engineer  
**Component**: `packaging/systemd/mdrap.service`, `Dockerfile`  
**Test Suite**: `tests/test_phase2_deployment.py`  

---

## 1. Systemd Service Hardening

The production unit `packaging/systemd/mdrap.service` incorporates comprehensive Linux kernel sandbox directives:

### 1.1 Process Identity & Privileges
- **Unprivileged User**: Runs strictly as `User=mdrap` and `Group=mdrap`. Never runs as `root`.
- **`NoNewPrivileges=true`**: Prevents the process and any spawned child from escalating privileges via SUID/SGID binaries.
- **`RestrictSUIDSGID=true`**: Disallows attempts to set SUID/SGID bits on files.

### 1.2 Filesystem Sandboxing
- **`ProtectSystem=strict`**: Mounts the entire OS filesystem (`/usr`, `/boot`, `/etc`, etc.) as read-only.
- **`ProtectHome=true`**: `/home`, `/root`, and `/run/user` are completely invisible (empty tmpfs).
- **`PrivateTmp=true`**: Provides an isolated `/tmp` namespace invisible to other processes.
- **`ReadWritePaths=/var/lib/mdrap /var/log/mdrap /dev/shm`**: Explicitly whitelists the only directories where MDRAP can write WAL segments, SQLite databases, logs, and POSIX shared memory segments.

### 1.3 Kernel Protection
- **`ProtectKernelTunables=true`**: Disallows modification of `/proc/sys`, `/sys`, etc.
- **`ProtectKernelModules=true`**: Disallows explicit loading or unloading of kernel modules.
- **`ProtectControlGroups=true`**: Mounts Linux cgroups as strictly read-only.

### 1.4 Resource Limits & Graceful Drain
- **`LimitNOFILE=65536`**: Ensures capacity for thousands of concurrent client TCP/WebSocket connections.
- **`LimitMEMLOCK=infinity`**: Permits memory locking for ultra-low latency IPC ring buffers in `/dev/shm`.
- **`TimeoutStopSec=10s`**: Allows the runtime `stop()` lifecycle sufficient time to flush WAL buffers and drain in-flight events before SIGKILL.
- **`Restart=on-failure`** with `RestartSec=2s`: Automatically restarts on unexpected exit while respecting supervisor backoffs.

---

## 2. Container Hardening (`Dockerfile`)

- **Multi-Stage Build**: Compiles wheels in a builder image and discards build tools, leaving a minimal runtime image.
- **Non-Root Execution**: Creates system group and user `mdrap (uid 1000, gid 1000)` and sets `USER mdrap`.
- **Healthcheck Integration**: Built-in `HEALTHCHECK --interval=30s --timeout=5s CMD curl -f http://127.0.0.1:8000/v1/health || exit 1`.
- **Zero World-Writable Directories**: Volume `/data` restricted to `chmod 750` owned by `mdrap:mdrap`.
