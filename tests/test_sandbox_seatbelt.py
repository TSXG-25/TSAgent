"""macOS Seatbelt sandbox backend — dispatch order + real confinement."""
import platform
import tempfile
from pathlib import Path
import pytest
from agent import sandbox


def _reset():
    sandbox._DOCKER_AVAILABLE = None
    sandbox._SEATBELT_AVAILABLE = None


def test_seatbelt_tier_between_docker_and_local(monkeypatch):
    """Docker 不可用时 Seatbelt 接管，不落到 _run_local。"""
    _reset()
    monkeypatch.setattr(sandbox, "_check_docker", lambda: False)
    monkeypatch.setattr(sandbox, "_check_seatbelt", lambda: True)
    monkeypatch.setattr(sandbox, "_run_seatbelt",
                        lambda *a, **k: "seatbelt-ok")
    monkeypatch.setattr(sandbox, "_run_local",
                        lambda *a, **k: pytest.fail("must not reach local"))
    assert sandbox.run_in_sandbox("true") == "seatbelt-ok"
    _reset()


def test_seatbelt_skipped_when_unavailable(monkeypatch):
    _reset()
    monkeypatch.setattr(sandbox, "_check_docker", lambda: False)
    monkeypatch.setattr(sandbox, "_check_seatbelt", lambda: False)
    monkeypatch.setenv(sandbox.LOCAL_EXECUTION_ENV, "0")
    out = sandbox.run_in_sandbox("echo hi")
    assert "EXECUTION_ENVIRONMENT_UNAVAILABLE" in out
    _reset()


@pytest.mark.skipif(platform.system() != "Darwin",
                    reason="Seatbelt is macOS-only")
def test_real_seatbelt_workspace_write():
    _reset()
    assert sandbox._check_seatbelt()
    ws = Path(tempfile.mkdtemp())
    # workspace 内可写
    out = sandbox._run_seatbelt("echo data > f.txt && cat f.txt", 10,
                                str(ws), writable_root=ws)
    assert "data" in out
    # workspace 外（$HOME）写被拒绝
    marker = Path.home() / "_seatbelt_denied_marker"
    marker.unlink(missing_ok=True)
    out = sandbox._run_seatbelt(f"touch {marker} && echo WROTE", 10,
                                str(ws), writable_root=ws)
    assert not marker.exists()
    _reset()


def test_profile_is_workspace_write():
    p = sandbox._SEATBELT_PROFILE
    assert "(deny file-write*)" in p
    assert '(subpath (param "ROOT"))' in p


def test_docker_network_defaults_none_and_opt_in(monkeypatch):
    monkeypatch.delenv(sandbox.SANDBOX_NETWORK_ENV, raising=False)
    assert sandbox._sandbox_network() == "none"
    monkeypatch.setenv(sandbox.SANDBOX_NETWORK_ENV, "bridge")
    assert sandbox._sandbox_network() == "bridge"
    monkeypatch.setenv(sandbox.SANDBOX_NETWORK_ENV, "garbage")
    assert sandbox._sandbox_network() == "none"


def test_seatbelt_preferred_tier_order(monkeypatch):
    """TSAGENT_SANDBOX_PREFER=seatbelt 时 Seatbelt 先于 Docker。"""
    monkeypatch.setenv(sandbox.SANDBOX_PREFER_ENV, "seatbelt")
    monkeypatch.setattr(sandbox, "_check_docker", lambda: True)
    monkeypatch.setattr(sandbox, "_check_seatbelt", lambda: True)
    monkeypatch.setattr(sandbox, "_run_docker",
                        lambda *a, **k: pytest.fail("must not reach docker"))
    monkeypatch.setattr(sandbox, "_run_seatbelt",
                        lambda *a, **k: "seatbelt-first")
    assert sandbox.run_in_sandbox("true") == "seatbelt-first"


def test_seatbelt_falls_back_to_docker_when_absent(monkeypatch):
    monkeypatch.setenv(sandbox.SANDBOX_PREFER_ENV, "seatbelt")
    monkeypatch.setattr(sandbox, "_check_docker", lambda: True)
    monkeypatch.setattr(sandbox, "_check_seatbelt", lambda: False)
    monkeypatch.setattr(sandbox, "_run_docker", lambda *a, **k: "docker-ok")
    assert sandbox.run_in_sandbox("true") == "docker-ok"


def test_dotfile_read_toggle(monkeypatch):
    import os
    monkeypatch.delenv(sandbox.SANDBOX_DOTFILES_ENV, raising=False)
    profile = sandbox._seatbelt_profile()
    assert "file-read*" in profile and "\\.env" in profile
    monkeypatch.setenv(sandbox.SANDBOX_DOTFILES_ENV, "allow")
    assert "file-read*" not in sandbox._seatbelt_profile()


@pytest.mark.skipif(platform.system() != "Darwin",
                    reason="Seatbelt is macOS-only")
def test_real_dotfile_read_denied(monkeypatch, tmp_path):
    probe = Path.home() / ".tsagent_seatbelt_probe"
    probe.write_text("secret")
    try:
        monkeypatch.delenv(sandbox.SANDBOX_DOTFILES_ENV, raising=False)
        out = sandbox._run_seatbelt(f"cat {probe}", 10, str(tmp_path),
                                    writable_root=tmp_path)
        assert "secret" not in out
        monkeypatch.setenv(sandbox.SANDBOX_DOTFILES_ENV, "allow")
        out = sandbox._run_seatbelt(f"cat {probe}", 10, str(tmp_path),
                                    writable_root=tmp_path)
        assert "secret" in out
    finally:
        probe.unlink(missing_ok=True)


def test_typeerror_is_non_retryable():
    from agent.llm import _retryable_provider_error
    assert _retryable_provider_error(TypeError("process() takes no kw")) is False
    assert _retryable_provider_error(SyntaxError("bad")) is False
