"""Mercury research-chain real evaluation driver.

Spawns a REAL local sidecar (`python3 -m agent.service.local_sidecar`) on the
same stdio JSONL wire the Tauri transport uses, with a real model
(TSAGENT_LLM_PROVIDER=ollama, OLLAMA_MODEL from env or qwen3:8b) and the REAL
DuckDuckGo MCP search provider. Three tasks exercise the offline-verified
chain end to end and record what the real model actually does:

- t1 "prefill": the faithful Navigator path — a real `web_search` RPC call,
  its sources formatted into the exact `mer.research-prefill` template, then
  a grounded run on that text.
- t2 "body": a task whose answer lives only inside a page body — the model
  must call web_fetch on the given URL to get it.
- t3 "fresh": a freshness-trigger request — the classifier should flag it
  and the run must retrieve (or honestly refuse) rather than answer from
  memory.

Checks are wire-observable only: tool observations in run events
(searched/fetched + source URLs the tools actually returned), the durable
output text, and citation accuracy (every cited URL must come from a
returned source or the provided request text — a URL nowhere in evidence
is a fabrication signal, not a citation).

Usage:
    python3 scripts/real_eval_mercury.py [--only t2] [--model qwen3:8b]

Reports land in realtest_reports/mercury-research-<ts>/. This is an
evaluation driver, not an offline test — it is intentionally NOT under
tests/.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO / ".env")

EVAL_ROOT = Path.home() / "Desktop" / "tsagent-real-eval"
REPORT_ROOT = REPO / "realtest_reports" / f"mercury-research-{time.strftime('%Y%m%d-%H%M%S')}"
RUN_DEADLINE_S = int(os.getenv("EVAL_DEADLINE_S", "900"))

# ASCII-only character class — CJK/full-width punctuation can never be part
# of a URL, so a citation can't smuggle trailing prose into the match.
URL_PATTERN = re.compile(r"https?://[A-Za-z0-9\-._~:/?#@!$&*+,;=%]+")
WEB_TOOLS = {"web_search", "web_news_search", "web_deep_search", "web_fetch"}
FETCH_TOOLS = {"web_fetch", "web_deep_search"}

# t2 answer lives only inside the page body — a snippet cannot supply the
# launch year in a form the model could rely on without reading the page.
TASKS = {
    "t2": {
        "name": "body-reading",
        "task": (
            "读取 https://en.wikipedia.org/wiki/DuckDuckGo 的正文，告诉我 "
            "DuckDuckGo 是哪一年推出的，并标注来源。"
        ),
        # Acceptance is task-shaped: this task is only met if the model
        # actually fetched THE GIVEN URL — searching without reading the
        # body (or fetching a different page) does not satisfy it.
        "required_fetch_url": "https://en.wikipedia.org/wiki/DuckDuckGo",
        "body_required": True,
    },
    "t3": {
        "name": "freshness",
        "task": "今天 AI 领域有什么值得注意的新闻？请带来源回答。",
    },
}


class Sidecar:
    """stdio JSONL client — the exact wire the desktop transport speaks."""

    def __init__(self, proc: asyncio.subprocess.Process):
        self.proc = proc
        self.seq = 0
        self.pending: dict[str, asyncio.Future] = {}

    @classmethod
    async def spawn(cls, db: Path) -> "Sidecar":
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        provider = os.environ.get("TSAGENT_LLM_PROVIDER", "ollama")
        env["TSAGENT_LLM_PROVIDER"] = provider
        if provider == "ollama":
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
        try:
            msg = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise RuntimeError(f"{method}: timed out after {timeout}s")
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


async def wait_terminal(sidecar: Sidecar, ident: dict, run_id: str) -> dict:
    deadline = time.monotonic() + RUN_DEADLINE_S
    while time.monotonic() < deadline:
        snap = await sidecar.call(
            "get_run", {**ident, "run_id": run_id}, timeout=120,
        )
        run = snap.get("run", snap)
        if str(run.get("status", "")) in {
            "COMPLETED", "FAILED_TERMINAL", "BLOCKED", "CANCELLED",
            "WAITING_USER",
        }:
            return run
        await asyncio.sleep(3)
    raise TimeoutError(
        f"run {run_id} did not reach a terminal status in {RUN_DEADLINE_S}s"
    )


def terminal_continuation(events: list[dict]) -> dict:
    for event in reversed(events):
        cont = (event.get("payload") or {}).get("continuation")
        if isinstance(cont, dict) and cont:
            return cont
    return {}


def observation_urls(observations: list[dict]) -> list[str]:
    """Source URLs the tools actually returned — from _sources when the
    executor collected them, else parsed out of the result content.
    FAILED observations contribute nothing: an error message that names
    the rejected URL is not evidence the source was read."""
    urls: list[str] = []
    for obs in observations:
        if str(obs.get("tool", "")) not in WEB_TOOLS:
            continue
        result = obs.get("result") or {}
        if result.get("ok") is not True:
            continue
        # A successful web_fetch's own URL is evidence — same rule the
        # executor applies when it records `_sources` for fetches.
        if str(obs.get("tool", "")) == "web_fetch":
            fetched = str((obs.get("input") or {}).get("url", "")).strip()
            if fetched:
                urls.append(fetched)
        for url in result.get("_sources") or ():
            if str(url).strip():
                urls.append(str(url).strip())
        content = str(result.get("content") or "")
        urls.extend(URL_PATTERN.findall(content))
    return list(dict.fromkeys(urls))


_ENV_ERROR_CODES = ("UNAVAILABLE", "TIMEOUT", "PROVIDER", "NETWORK", "CONNECTION")
_ENV_CONTENT_MARKERS = ("ssrf", "non-public", "private/internal", "refusing to fetch")


def _fetch_failure_is_env(result: dict) -> bool:
    """A fetch failure is environmental only when the error carries a known
    environment signature — SSRF/private-address refusals and network or
    provider unavailability. Argument/validation errors are the model's
    mistake, not the environment's."""
    code = str(result.get("error_code") or "").upper()
    if any(k in code for k in _ENV_ERROR_CODES):
        return True
    content = str(result.get("content") or "").lower()
    return any(m in content for m in _ENV_CONTENT_MARKERS)


def compute_checks(
    observations: list[dict], output_text: str, request_text: str,
    spec: dict,
) -> dict:
    """Wire-observable checks shared by live evaluation and --recompute."""
    web_obs = [
        {
            "tool": str(o.get("tool", "")),
            "args": o.get("input") or {},
            "ok": (o.get("result") or {}).get("ok") is True,
            "error_code": str((o.get("result") or {}).get("error_code") or ""),
        }
        for o in observations if str(o.get("tool", "")) in WEB_TOOLS
    ]
    required_url = str(spec.get("required_fetch_url") or "")
    # "Attempted" and "succeeded" are different facts — an SSRF-rejected
    # fetch is an attempt, not a body read.
    fetch_attempted = any(o["tool"] in FETCH_TOOLS for o in web_obs)
    fetched_body = any(
        o["tool"] in FETCH_TOOLS and o["ok"] for o in web_obs
    )
    fetch_results = [
        (o.get("result") or {}) for o in observations
        if str(o.get("tool", "")) in FETCH_TOOLS
    ]
    failed_fetches = [r for r in fetch_results if r.get("ok") is not True]
    fetch_env_blocked = bool(
        failed_fetches
        and all(_fetch_failure_is_env(r) for r in failed_fetches)
    )
    required_attempted = bool(
        required_url
        and any(
            o["tool"] in FETCH_TOOLS
            and str((o["args"] or {}).get("url", "")).rstrip("/")
            == required_url.rstrip("/")
            for o in web_obs
        )
    )
    fetched_required = bool(
        required_url
        and required_attempted
        and any(
            o["tool"] in FETCH_TOOLS
            and o["ok"]
            and str((o["args"] or {}).get("url", "")).rstrip("/")
            == required_url.rstrip("/")
            for o in web_obs
        )
    )
    cited = list(dict.fromkeys(URL_PATTERN.findall(output_text)))
    evidence_urls = observation_urls(observations)
    provided_urls = URL_PATTERN.findall(request_text)
    known = set(evidence_urls) | set(provided_urls)
    fabricated = [
        u for u in cited
        if not any(u == k or u.rstrip("/") == k.rstrip("/") for k in known)
    ]
    return {
        "searched": any(
            o["tool"] in {"web_search", "web_news_search", "web_deep_search"}
            and o["ok"]
            for o in web_obs
        ),
        "fetch_attempted": fetch_attempted,
        "fetch_env_blocked": fetch_env_blocked,
        "fetched_body": fetched_body,
        "fetched_required_url": fetched_required,
        "required_fetch_attempted": required_attempted,
        "web_tool_calls": web_obs,
        "evidence_urls": evidence_urls,
        "cited_urls": cited,
        "fabricated_urls": fabricated,
    }


def classify(run_status: str, checks: dict, spec: dict,
             sidecar_error: str = "") -> str:
    """capability / honest refusal / environment — acceptance is shaped by
    what the task actually required, not by a single 'searched' flag.
    A grounded task that fails with RESEARCH_TOOL_UNAVAILABLE is the
    contract working; a fetch-required task must have fetched the given
    URL, not merely searched."""
    if sidecar_error:
        return (
            "deadline_exceeded"
            if "did not reach a terminal status" in sidecar_error
            else "provider_or_environment"
        )
    if run_status == "COMPLETED":
        # Body-required tasks are only met by a body that was actually
        # READ — a fetch attempt rejected by the environment does not
        # satisfy it, and a search fallback is not a body read.
        if spec.get("body_required") and not checks.get("fetched_body"):
            if checks.get("fetch_env_blocked"):
                return "provider_or_environment"
            return "capability_gap"
        if spec.get("required_fetch_url") and not checks.get("fetched_required_url"):
            return "capability_gap"
        retrieved = checks.get("searched") or checks.get("fetched_body")
        if retrieved and checks.get("cited_urls") and not checks.get("fabricated_urls"):
            return "capability_met"
        return "capability_gap"
    if run_status in {"FAILED_TERMINAL", "BLOCKED"}:
        code = str(checks.get("failure_code", ""))
        missing = checks.get("goal_missing") or []
        if "PROVIDER" in code or any("PROVIDER" in str(m) for m in missing):
            return "provider_or_environment"
        if "RESEARCH_TOOL_UNAVAILABLE" in code or missing:
            return "capability_gap_honest_refusal"
        return "capability_gap"
    return "runtime_contract_gap"


async def evaluate(
    sidecar: Sidecar, key: str, spec: dict, request_text: str,
) -> dict:
    ident = {
        "tenant_id": "t-eval", "user_id": "u-eval",
        "session_id": f"s-mercury-eval-{key}", "request_id": f"req-{key}-1",
    }
    result: dict = {
        "task_id": key, "name": spec["name"], "task": request_text,
        "classification": "", "checks": {},
    }
    try:
        await sidecar.call("create_session", {**ident, "planet": "mercury"})
        await sidecar.call("upsert_workspace", {
            **ident, "workspace_id": f"ws-eval-{key}",
            "display_name": spec["name"],
            "path": str(EVAL_ROOT / ".sidecar-ws"),
        })
        await sidecar.call("assign_session_workspace", {
            **ident, "workspace_id": f"ws-eval-{key}",
        })
        started = time.monotonic()
        handle = await sidecar.call("start_run", {
            **ident, "request_text": request_text,
            "origin_planet": "mercury",
        }, timeout=120)
        run_id = str(
            (handle.get("run") or handle).get("run_id") or handle.get("run_id")
        )
        result["run_id"] = run_id
        run = await wait_terminal(sidecar, ident, run_id)
        result["elapsed_s"] = round(time.monotonic() - started, 1)
        status = str(run.get("status", ""))
        result["terminal_status"] = status
        result["failure"] = run.get("failure")

        events = await sidecar.call("read_events", {
            **ident, "run_id": run_id, "after_sequence": 0,
        }, timeout=120)
        event_list = events if isinstance(events, list) else events.get("events", [])
        cont = terminal_continuation(event_list)
        observations = cont.get("observations") or []
        output_text = str((run.get("output") or {}).get("text") or "")
        failure_code = str(
            ((run.get("failure_summary") or {}) or {}).get("code")
            or (run.get("failure") or "")
        )
        checks = compute_checks(
            observations, output_text, request_text, spec,
        )
        checks["goal_missing"] = cont.get("missing", [])
        checks["output_excerpt"] = output_text[:1500]
        checks["failure_code"] = failure_code
        result["checks"] = checks
        result["classification"] = classify(status, checks, spec)
    except Exception as exc:  # provider/env/transport failures land here
        error = str(exc) or type(exc).__name__
        result["classification"] = classify(
            "", {}, spec, sidecar_error=error
        )
        result["error"] = error[:500]
    return result


async def t1_prefill_text(sidecar: Sidecar) -> tuple[str, list[str]]:
    """The faithful Navigator path: real web_search RPC → exact
    `mer.research-prefill` zh template with the returned sources."""
    ident = {
        "tenant_id": "t-eval", "user_id": "u-eval",
        "session_id": "s-mercury-eval-t1", "request_id": "req-t1-search",
    }
    res = await sidecar.call("web_search", {
        **ident, "query": "DuckDuckGo 搜索引擎 隐私", "max_results": 4,
    }, timeout=120)
    sources = res.get("sources") or []
    lines = "\n".join(
        f"- {s.get('title') or s.get('url') or 'source'} — {s.get('url', '')}"
        for s in sources
    )
    prefill = (
        "基于以下搜索结果继续研究，阅读来源正文后回答：\n"
        "查询：DuckDuckGo 搜索引擎 隐私\n"
        f"{lines}\n\n"
        "研究目标：总结这些来源如何描述 DuckDuckGo 的隐私策略，并标注来源。"
    )
    return prefill, [str(s.get("url", "")) for s in sources]


def recompute_report(report_dir: Path) -> int:
    """Recompute checks/classification for an existing report from its
    eval.db events — corrects archived stats without any model calls."""
    import sqlite3

    report_path = report_dir / "report.json"
    db_path = report_dir / "eval.db"
    report = json.loads(report_path.read_text())
    db = sqlite3.connect(str(db_path))
    try:
        for r in report.get("results") or []:
            task_id = str(r.get("task_id") or r.get("candidate") or "")
            spec = TASKS.get(task_id) or {
                "name": r.get("name", task_id), "body_required": True,
            }
            run_id = str(r.get("run_id") or "")
            status = str(r.get("terminal_status") or "")
            row = db.execute(
                "SELECT text FROM run_outputs WHERE run_id = ? "
                "ORDER BY revision DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            output_text = str(row[0]) if row else ""
            events = db.execute(
                "SELECT payload_json FROM run_events WHERE run_id = ? "
                "ORDER BY sequence_number",
                (run_id,),
            ).fetchall()
            observations: list[dict] = []
            for (payload_json,) in events:
                payload = json.loads(payload_json or "{}")
                cont = payload.get("continuation") or {}
                observations.extend(cont.get("observations") or [])
            checks = compute_checks(
                observations, output_text,
                str(r.get("task") or ""), spec,
            )
            # Preserve fields recompute cannot derive differently.
            for key in ("goal_missing", "output_excerpt", "failure_code",
                        "prefill_source_urls"):
                if key in r.get("checks", {}):
                    checks[key] = r["checks"][key]
            checks["recomputed"] = (
                "checks regenerated from eval.db observations with "
                "attempted-vs-succeeded fetch distinction; failed results "
                "no longer contribute evidence URLs"
            )
            r["checks"] = checks
            r["classification"] = classify(status, checks, spec)
        report["recomputed"] = True
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2)
        )
        for r in report.get("results") or []:
            print(
                f"{r.get('task_id')}: {r['classification']} "
                f"(fetched_body={r['checks'].get('fetched_body')}, "
                f"fabricated={r['checks'].get('fabricated_urls')})"
            )
        return 0
    finally:
        db.close()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=["t1", "t2", "t3"], default=None)
    parser.add_argument(
        "--recompute",
        metavar="REPORT_DIR",
        help="recompute checks/classification for an existing report "
        "directory from its eval.db events — no model calls",
    )
    args = parser.parse_args()

    if args.recompute:
        return recompute_report(Path(args.recompute))

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    sidecar = await Sidecar.spawn(REPORT_ROOT / "eval.db")
    results = []
    try:
        health = await sidecar.call("health", {})
        print("sidecar health:", health)

        if args.only in (None, "t1"):
            print("\n=== t1: prefill entry (real web_search RPC → template → run)")
            prefill, search_urls = await t1_prefill_text(sidecar)
            r = await evaluate(
                sidecar, "t1",
                {
                    "name": "prefill-entry", "task": prefill,
                    "body_required": True,
                },
                prefill,
            )
            r["checks"]["prefill_source_urls"] = search_urls
            results.append(r)
            print(json.dumps(r, ensure_ascii=False, indent=2))

        for key, spec in TASKS.items():
            if args.only and key != args.only:
                continue
            print(f"\n=== {key}: {spec['name']}")
            r = await evaluate(sidecar, key, spec, spec["task"])
            results.append(r)
            print(json.dumps(r, ensure_ascii=False, indent=2))
    finally:
        await sidecar.shutdown()

    provider = os.environ.get("TSAGENT_LLM_PROVIDER", "auto")
    report = {
        "driver": "scripts/real_eval_mercury.py",
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
    sys.exit(asyncio.run(main()))
