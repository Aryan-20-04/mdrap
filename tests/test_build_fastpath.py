import subprocess
import sys

import build_fastpath


def test_build_core_links_math_library_for_gcc_like_compilers(tmp_path, monkeypatch):
    c_source = tmp_path / "mdrap_core.c"
    c_source.write_text("int main(void) { return 0; }\n", encoding="utf-8")

    captured = {}
    out_bin = tmp_path / build_fastpath.get_core_bin_name()

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        out_bin.write_bytes(b"binary")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(build_fastpath, "_find_compiler", lambda: "gcc")
    monkeypatch.setattr(build_fastpath.subprocess, "run", fake_run)

    assert build_fastpath.build_core(target_dir=str(tmp_path), quiet=True) is True

    cmd = captured["cmd"]
    if sys.platform == "win32":
        assert "-lm" not in cmd
    else:
        assert "-lm" in cmd
