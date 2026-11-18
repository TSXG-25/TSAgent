"""Mars dev-loop real evaluation driver (MC-06 handoff candidates R1-R3).

Spawns a REAL local sidecar (`python3 -m agent.service.local_sidecar`) on the
same stdio JSONL wire the Tauri transport uses, with a real model
(TSAGENT_LLM_PROVIDER=ollama, OLLAMA_MODEL from env or qwen3:8b). For each
candidate it materializes an isolated git project, binds it to a
mars-claimed session, starts a real run, waits for the terminal event, then
verifies:

- runtime contract: terminal status + event payload shape
- capability: the acceptance command actually passes, agent edits attributed
- provider/environment: failures classified separately

Usage:
    python3 scripts/real_eval_mars.py [--only r1] [--model qwen3:8b]

Reports land in realtest_reports/mars-devloop-<ts>/. This is an evaluation
driver, not an offline test — it is intentionally NOT under tests/.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO))

from test_mars_devloop import FIX_BUG, SMALL_FEATURE, REFACTOR, _materialize  # noqa: E402

from dotenv import load_dotenv  # noqa: E402

# Match the sidecar's own env resolution so report metadata reflects what the
# provider actually ran (e.g. .env MODEL_NAME under the deepseek provider).
load_dotenv(REPO / ".env")

EVAL_ROOT = Path.home() / "Desktop" / "tsagent-real-eval"
REPORT_ROOT = REPO / "realtest_reports" / f"mars-devloop-{time.strftime('%Y%m%d-%H%M%S')}"
RUN_DEADLINE_S = int(
    os.getenv("EVAL_DEADLINE_S", "900")
)  # qwen3:8b on a laptop is slow; DeepSeek needs more — EVAL_DEADLINE_S=1800

CANDIDATES = {
    "r1": FIX_BUG,
    "r2": SMALL_FEATURE,
    "r3": REFACTOR,
}


class Sidecar:
    """stdio JSONL client — the exact wire the desktop transport speaks."""

    def __init__(self, proc: asyncio.subprocess.Process):
        self.proc = proc
        self.seq = 0
        self.pending: dict[str, asyncio.Future] = {}
        self.buffer = ""

    @classmethod
    async def spawn(cls, db: Path) -> "Sidecar":
        env = {
            **os.environ,
            "PYTHONUNBUFFERED": "1",
        }
        provider = os.environ.get("TSAGENT_LLM_PROVIDER", "ollama")
        env["TSAGENT_LLM_PROVIDER"] = provider
        if provider == "ollama":
            # Offline evals must not accidentally spend the real API key.
            env["OPENAI_API_KEY"] = ""
            env["LLM_API_KEY"] = ""
            env["P2_SECONDARY_API_KEY"] = ""
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-u", "-m", "agent.service.local_sidecar",
            "--database", str(db),
            "--workspace-root", str(EVAL_ROOT / ".sidecar-ws"),
            cwd=REPO, env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        sidecar = cls(proc)
        asyncio.create_task(sidecar._reader())
        asyncio.create_task(sidecar._stderr())
        return sidecar

    async def _reader(self) -> None:
        assert self.proc.stdout is not None
        async for raw in self.proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            fut = self.pending.pop(str(msg.get("id", "")), None)
            if fut is not None and not fut.done():
                fut.set_result(msg)

    async def _stderr(self) -> None:
        assert self.proc.stderr is not None
        async for raw in self.proc.stderr:
            sys.stderr.write(f"[sidecar] {raw.decode('utf-8', 'replace')}")
            sys.stderr.flush()

    async def call(self, method: str, params: dict, timeout: float = 60.0):
        self.seq += 1
        req_id = f"eval-{self.seq}"
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self.pending[req_id] = fut
        assert self.proc.stdin is not None
        self.proc.stdin.write(
            (json.dumps({"id": req_id, "method": method, "params": params}) + "\n").encode()
        )
        await self.proc.stdin.drain()
        msg = await asyncio.wait_for(fut, timeout)
        resp = msg.get("response", msg)
        if resp.get("ok") is True:
            return resp.get("result")
        raise RuntimeError(f"{method}: {resp.get('error') or resp}")

    async def shutdown(self) -> None:
        try:
            await self.call("shutdown", {}, timeout=10)
        except Exception:
            pass
        try:
            self.proc.terminate()
            await asyncio.wait_for(self.proc.wait(), 10)
        except Exception:
            self.proc.kill()


def materialize_candidates() -> dict[str, Path]:
    if EVAL_ROOT.exists():
        shutil.rmtree(EVAL_ROOT)
    EVAL_ROOT.mkdir(parents=True)
    roots = {}
    for key, spec in CANDIDATES.items():
        roots[key] = _materialize(spec, EVAL_ROOT / f"{key}-{spec['name']}")
    return roots


async def wait_terminal(sidecar: Sidecar, ident: dict, run_id: str) -> dict:
    deadline = time.monotonic() + RUN_DEADLINE_S
    while time.monotonic() < deadline:
        snap = await sidecar.call("get_run", {**ident, "run_id": run_id})
        run = snap.get("run", snap)
        status = str(run.get("status", ""))
        if status in {"COMPLETED", "FAILED_TERMINAL", "BLOCKED", "CANCELLED",
                      "WAITING_USER"}:
            return run
        await asyncio.sleep(3)
    raise TimeoutError(f"run {run_id} did not reach a terminal status in {RUN_DEADLINE_S}s")


def terminal_continuation(events: list[dict]) -> dict:
    for event in reversed(events):
        cont = (event.get("payload") or {}).get("continuation")
        if isinstance(cont, dict) and cont:
            return cont
    return {}


def classify(run_status: str, accepted: bool, cont: dict,
             sidecar_error: str = "") -> str:
    """Four-way outcome split: contract / capability / provider / environment.

    A FAILED_TERMINAL whose goal verifier refused an unverified claim is the
    contract working correctly — classify by whether the refusal was honest,
    not by the raw status."""
    if sidecar_error:
        return (
            "deadline_exceeded"
            if "did not reach a terminal status" in sidecar_error
            else "provider_or_environment"
        )
    missing = list((cont.get("missing") or []))
    if run_status == "COMPLETED" and accepted and not missing:
        return "capability_met"
    if run_status == "COMPLETED" and not missing:
        return "capability_gap"
    # terminal failure with a goal-verified gap = honest refusal, not a
    # contract break; a missing/empty verifier block is the real gap
    if missing:
        return "capability_gap_honest_refusal" if not accepted else (
            "capability_met_unverified_claim_blocked"
        )
    return "runtime_contract_gap"


async def evaluate(sidecar: Sidecar, key: str, spec: dict, root: Path) -> dict:
    ident = {
        "tenant_id": "t-eval", "user_id": "u-eval",
        "session_id": f"s-mars-eval-{key}", "request_id": f"req-{key}-1",
    }
    result: dict = {
        "candidate": key, "fixture": spec["name"], "dir": str(root),
        "task": spec["task"], "classification": "", "checks": {},
    }
    try:
        await sidecar.call("create_session", {**ident, "planet": "mars"})
        await sidecar.call("upsert_workspace", {
            **ident, "workspace_id": f"ws-eval-{key}",
            "display_name": spec["name"], "path": str(root),
        })
        await sidecar.call("assign_session_workspace", {
            **ident, "workspace_id": f"ws-eval-{key}",
        })
        started = time.monotonic()
        handle = await sidecar.call("start_run", {
            **ident, "request_text": spec["task"], "origin_planet": "mars",
        }, timeout=120)
        run_id = str((handle.get("run") or handle).get("run_id") or handle.get("run_id"))
        result["run_id"] = run_id
        run = await wait_terminal(sidecar, ident, run_id)
        result["elapsed_s"] = round(time.monotonic() - started, 1)
        result["terminal_status"] = run.get("status")
        result["failure"] = run.get("failure")

        events = await sidecar.call("read_events", {
            **ident, "run_id": run_id, "after_sequence": 0,
        })
        event_list = events if isinstance(events, list) else events.get("events", [])
        cont = terminal_continuation(event_list)
        changes = cont.get("workspace_changes") or {}
        observations = cont.get("observations") or []

        preserved = {
            path: (root / path).read_text() == text
            for path, text in {**spec["user_edit"], **spec["untracked"]}.items()
        }
        accepted = bool(spec["accept"](root))
        result["checks"] = {
            "accepted": accepted,
            "preserved_user_files": preserved,
            "workspace_changes_present": bool(changes),
            "agent_files": changes.get("agent_files", []),
            "ambiguous_files": changes.get("ambiguous_files", []),
            "user_dirty_preserved": changes.get("user_dirty_preserved", []),
            "shell_observations": [
                {
                    "cmd": (o.get("input") or {}).get("cmd", ""),
                    "ok": (o.get("result") or {}).get("ok"),
                    "error_code": (o.get("result") or {}).get("error_code", ""),
                }
                for o in observations if o.get("tool") == "shell"
            ],
        }
        result["checks"]["goal_missing"] = cont.get("missing", [])
        result["classification"] = classify(
            str(run.get("status")), accepted, cont,
        )
    except Exception as exc:  # provider/env/transport failures land here
        result["classification"] = classify("", False, {}, sidecar_error=str(exc))
        result["error"] = str(exc)[:500]
    return result


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=list(CANDIDATES), default=None)
    args = parser.parse_args()

    roots = materialize_candidates()
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    sidecar = await Sidecar.spawn(REPORT_ROOT / "eval.db")
    results = []
    try:
        health = await sidecar.call("health", {})
        print("sidecar health:", health)
        for key, spec in CANDIDATES.items():
            if args.only and key != args.only:
                continue
            print(f"\n=== {key}: {spec['name']} → {roots[key]}")
            result = await evaluate(sidecar, key, spec, roots[key])
            results.append(result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        await sidecar.shutdown()

    provider = os.environ.get("TSAGENT_LLM_PROVIDER", "auto")
    report = {
        "driver": "scripts/real_eval_mars.py",
        "provider": provider,
        "model": (
            os.environ.get("MODEL_NAME", "deepseek-v4-flash")
            if provider == "deepseek"
            else os.environ.get("OLLAMA_MODEL", "qwen3:8b")
        ),
        "results": results,
    }
    (REPORT_ROOT / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    print(f"\nreport → {REPORT_ROOT / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
