"""
Phase 2 Deployment & Linux Hardening Verification Tests (Workstream F).

Verifies:
1. packaging/systemd/mdrap.service exists and contains mandatory security directives.
2. Dockerfile adheres to non-root execution and includes healthcheck probe.
"""

from pathlib import Path
import configparser


def test_systemd_unit_file_security_hardening():
    """Verify systemd service unit contains required security and resource directives."""
    unit_path = Path("packaging/systemd/mdrap.service")
    assert unit_path.is_file(), f"Systemd unit file not found at {unit_path}"

    content = unit_path.read_text(encoding="utf-8")
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read_string(content)

    assert parser.has_section("Unit")
    assert parser.has_section("Service")
    assert parser.has_section("Install")

    service = parser["Service"]
    
    # 1. Non-root user
    assert service.get("User") == "mdrap"
    assert service.get("Group") == "mdrap"

    # 2. Linux Sandboxing & Hardening
    assert service.get("ProtectSystem") == "strict"
    assert service.get("ProtectHome") == "true"
    assert service.get("PrivateTmp") == "true"
    assert service.get("NoNewPrivileges") == "true"
    assert service.get("ProtectKernelModules") == "true"
    assert service.get("ProtectControlGroups") == "true"

    # 3. Resource Limits & Drain Timeout
    assert int(service.get("LimitNOFILE", "0")) >= 65536
    assert "TimeoutStopSec" in service

    # 4. Writable paths
    rw_paths = service.get("ReadWritePaths", "")
    assert "/var/lib/mdrap" in rw_paths
    assert "/dev/shm" in rw_paths


def test_dockerfile_security_and_non_root():
    """Verify Dockerfile runs as unprivileged non-root user and specifies healthcheck."""
    dockerfile_path = Path("Dockerfile")
    assert dockerfile_path.is_file(), f"Dockerfile not found at {dockerfile_path}"

    lines = [line.strip() for line in dockerfile_path.read_text(encoding="utf-8").splitlines()]
    
    user_directives = [line for line in lines if line.startswith("USER")]
    assert any("mdrap" in u for u in user_directives), "Dockerfile must declare USER mdrap"

    healthcheck_directives = [line for line in lines if line.startswith("HEALTHCHECK")]
    assert len(healthcheck_directives) > 0, "Dockerfile must declare HEALTHCHECK"
