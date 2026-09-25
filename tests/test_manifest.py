import json
import os
from manifest import create_run_manifest, write_run_manifest


def test_manifest_creation_and_serialization(tmp_path):
    manifest = create_run_manifest(
        config_hash="abc12345",
        seed=42,
        num_events=50000,
        elapsed_s=1.25,
    )
    assert manifest["manifest_version"] == "1.0.0"
    assert manifest["config_sha256"] == "abc12345"
    assert manifest["execution"]["events_processed"] == 50000
    assert manifest["execution"]["throughput_eps"] == 40000.0
    assert "git_commit" in manifest
    assert "python" in manifest
    assert "system" in manifest

    out_file = str(tmp_path / "manifest.json")
    written_path = write_run_manifest(manifest, out_file)
    assert os.path.exists(written_path)

    with open(written_path, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    assert loaded["execution"]["events_processed"] == 50000
