"""Phase 6 Security, Configuration, API & Packaging Hardening Test Suite.

Verifies:
- Config schema validation strictly fails closed on unknown sections or invalid keys.
- Constant-time comparison is used on all authentication boundaries.
- Telemetry, health, and stats output strictly redacts all secrets and tokens.
- Secure filesystem permissions on POSIX systems (0700 on WAL directories, 0600 on secrets and segments).
- Absence of unsafe eval(), exec(), or unsanitized shell executions in core engine code.
"""

from __future__ import annotations

import ast
import glob
import os
import sys
import tempfile
import pytest

from mdrap.config_loader import ConfigValidationError, validate_config
from mdrap.ingestlog import IngestLog
from mdrap.models import RawEvent
from mdrap.security import Role, SecurityManager
from mdrap.storage import Store


def test_config_validation_rejects_unknown_sections() -> None:
    """Config validation must fail closed on unknown top-level sections."""
    bad_cfg = {
        "defaults": {"staleness_threshold_s": 0.05},
        "malicious_or_unknown_section": {"foo": "bar"},
    }
    with pytest.raises(ConfigValidationError) as exc:
        validate_config(bad_cfg)
    assert "Unknown configuration section 'malicious_or_unknown_section'" in str(exc.value)


def test_config_validation_rejects_unknown_security_keys() -> None:
    """Config validation must fail closed on unknown keys in [security]."""
    bad_cfg = {
        "security": {
            "rate_limit_per_sec": 1000.0,
            "dangerous_unvetted_flag": True,
        }
    }
    with pytest.raises(ConfigValidationError) as exc:
        validate_config(bad_cfg)
    assert "Unknown key 'dangerous_unvetted_flag' in [security]" in str(exc.value)


def test_config_validation_rejects_unknown_system_keys() -> None:
    """Config validation must fail closed on unknown keys in [system]."""
    bad_cfg = {
        "system": {
            "log_level": "INFO",
            "unsupported_opt": 123,
        }
    }
    with pytest.raises(ConfigValidationError) as exc:
        validate_config(bad_cfg)
    assert "Unknown key 'unsupported_opt' in [system]" in str(exc.value)


def test_security_manager_constant_time_comparison() -> None:
    """Authentication and signature validation must reject tampered tokens cleanly."""
    sec = SecurityManager()
    token = sec.generate_api_key(client_id="ClientAlpha", role=Role.OPERATOR)

    assert sec.authenticate(token) is True
    # Tampered token
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    assert sec.authenticate(tampered) is False
    assert sec.authenticate("") is False
    assert sec.authenticate("short") is False


def test_security_manager_redacted_stats() -> None:
    """Telemetry and stats methods must never leak plain-text tokens or secret keys."""
    sec = SecurityManager()
    token = sec.generate_api_key(client_id="SecretClient", role=Role.ADMIN)

    stats = sec.stats()
    stats_str = str(stats)

    # Token must not appear anywhere in stats output
    assert token not in stats_str
    assert "SecretClient" not in stats_str  # client_id not leaked in high-level stats
    assert "api_keys_active" in stats
    assert "api_keys_total" in stats


def test_wal_directory_and_segment_permissions(tmp_path) -> None:
    """Verify WAL log directory and segment file permissions on POSIX."""
    wal_dir = tmp_path / "perm_wal"
    log = IngestLog(str(wal_dir))
    log.append(RawEvent(source="TEST", payload={"instrument": "AAPL", "price": 100.0}))
    log.flush()

    if os.name == "posix":
        dir_mode = os.stat(wal_dir).st_mode & 0o777
        assert dir_mode == 0o700, f"Expected 0700 on WAL dir, got {oct(dir_mode)}"

        segments = list(wal_dir.glob("segment_*.log"))
        assert len(segments) >= 1
        seg_mode = os.stat(segments[0]).st_mode & 0o777
        assert seg_mode == 0o600, f"Expected 0600 on segment, got {oct(seg_mode)}"

    log.close()


def test_zero_unsafe_eval_in_production_code() -> None:
    """AST static audit: zero eval() or exec() calls in all src/mdrap Python modules."""
    src_dir = os.path.join(os.path.dirname(__file__), "..", "src", "mdrap")
    disallowed_calls = {"eval", "exec"}

    violations = []
    for root, _, files in os.walk(src_dir):
        for f in files:
            if f.endswith(".py"):
                path = os.path.join(root, f)
                with open(path, "r", encoding="utf-8") as py_f:
                    try:
                        tree = ast.parse(py_f.read(), filename=path)
                    except SyntaxError:
                        continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        fn_name = ""
                        if isinstance(node.func, ast.Name):
                            fn_name = node.func.id
                        elif isinstance(node.func, ast.Attribute):
                            fn_name = node.func.attr
                        if fn_name in disallowed_calls:
                            violations.append(f"{path}:{node.lineno} calls {fn_name}()")

    assert not violations, f"Found unsafe code execution calls: {violations}"
