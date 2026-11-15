"""Cold-start tool assembly — the toolset must expose shell/python schemas
exactly when the Run's authorization grants them, even under a bare
sidecar bootstrap (bootstrap_tools=False loads web only).

Runs in a subprocess so the process-global tool registry starts cold —
an in-process test can never unload an already-imported tools.* module."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]

_PROBE = """
import json, os, sys
from pathlib import Path
from types import SimpleNamespace

from agent.service.local_sidecar.lifecycle import SidecarConfig, create_service
from agent.loop.toolset import build_toolset

create_service(
    SidecarConfig(
        database_path=Path(os.environ["PROBE_DB"]),
        workspace_root=Path(os.environ["PROBE_WS"]),
    ),
    bootstrap_tools=False,
    defer_context_creation=True,
)

def names(auth):
    return sorted(build_toolset(auth).keys())

auth = SimpleNamespace(
    allows_file_mutation=True,
    command_execution_allowed=True,
    code_execution_allowed=True,
)
denied = SimpleNamespace(
    allows_file_mutation=True,
    command_execution_allowed=False,
    code_execution_allowed=False,
)
print("PROBE_RESULT:" + json.dumps({
    "authorized": names(auth),
    "denied": names(denied),
}))
"""


def _cold_probe(script: str, tmp_path: Path) -> dict[str, object]:
    env = dict(os.environ)
    env["PROBE_DB"] = str(tmp_path / "runtime.sqlite")
    env["PROBE_WS"] = str(tmp_path / "ws")
    code = f"import sys; sys.path.insert(0, {str(ROOT)!r})\n" + script
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    marker = "PROBE_RESULT:"
    line = next(
        item for item in proc.stdout.splitlines() if item.startswith(marker)
    )
    return json.loads(line[len(marker):])


def test_cold_sidecar_exposes_shell_and_python_only_with_grant(tmp_path):
    out = _cold_probe(_PROBE, tmp_path)
    # 授权授予 → schema 在目录里（此前冷启动只装 web，授权了也拿不到）。
    assert "shell" in out["authorized"]
    assert "run_python" in out["authorized"]
    # 未授予 → 即使模块已导入也不出现在目录。
    assert "shell" not in out["denied"]
    assert "run_python" not in out["denied"]
