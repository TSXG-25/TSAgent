"""Ad-hoc real smoke for browser_* tools through the sidecar (Mercury run).

Run with the project .venv (playwright installed).  Writes result.json +
eval.db into REPORT_DIR; tee sidecar stderr into REPORT_DIR yourself."""
import asyncio, json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scripts.real_eval_mercury as m
m.REPORT_ROOT = Path(sys.argv[1])  # usage: real_smoke_browser.py REPORT_DIR "task text"
TASK = sys.argv[2]

async def main():
    m.REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    sc = await m.Sidecar.spawn(m.REPORT_ROOT / "eval.db")
    ident = {"tenant_id": "t-eval", "user_id": "u-eval",
             "session_id": "s-browser-smoke", "request_id": "req-b-1"}
    try:
        await sc.call("create_session", {**ident, "planet": "mercury"})
        await sc.call("upsert_workspace", {**ident, "workspace_id": "ws-b",
            "display_name": "browser", "path": str(m.EVAL_ROOT / ".sidecar-ws")})
        await sc.call("assign_session_workspace", {**ident, "workspace_id": "ws-b"})
        t0 = time.monotonic()
        h = await sc.call("start_run", {**ident, "request_text": TASK,
                                        "origin_planet": "mercury"}, timeout=120)
        run_id = str((h.get("run") or h).get("run_id") or h.get("run_id"))
        run = await m.wait_terminal(sc, ident, run_id)
        ev = await sc.call("read_events", {**ident, "run_id": run_id, "after_sequence": 0}, timeout=120)
        ev = ev if isinstance(ev, list) else ev.get("events", [])
        typed = [(str(e.get("type") or e.get("event_type") or ""), e.get("payload") or {}) for e in ev]
        obs = m.collect_observations(typed)
        out = {
            "run_id": run_id, "status": run.get("status"),
            "elapsed_s": round(time.monotonic() - t0, 1),
            "tool_calls": [{"tool": o.get("tool"), "input": o.get("input"),
                            "ok": (o.get("result") or {}).get("ok"),
                            "error_code": (o.get("result") or {}).get("error_code"),
                            "content": str((o.get("result") or {}).get("content") or "")[:200]}
                           for o in obs],
            "output": str((run.get("output") or {}).get("text") or "")[:800],
        }
        (m.REPORT_ROOT / "result.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
        print(json.dumps(out, ensure_ascii=False, indent=2))
    finally:
        await sc.shutdown()
asyncio.run(main())
