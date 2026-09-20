import os
import pytest
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from security import SecurityManager


def test_s1_no_hardcoded_secrets_without_demo(monkeypatch, tmp_path):
    monkeypatch.delenv("MDRAP_DEMO", raising=False)
    monkeypatch.delenv("MDRAP_REQUIRE_ENV_SECRETS", raising=False)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))

    sec = SecurityManager()
    assert "mdrap_demo_key" not in sec._api_keys
    assert not hasattr(SecurityManager, "DEFAULT_SECRETS") or len(getattr(SecurityManager, "DEFAULT_SECRETS", {})) == 0


def test_s1_demo_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("MDRAP_DEMO", "1")
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))

    sec = SecurityManager()
    assert "mdrap_demo_key" in sec._api_keys
