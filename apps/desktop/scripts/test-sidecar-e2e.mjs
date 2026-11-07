/** Real-sidecar E2E — spawns `python3 -m agent.service.local_sidecar` on
 *  stdio JSONL (the same wire the Tauri transport uses), then exercises the
 *  RPC surface the desktop workspaces depend on. Offline: no LLM keys or
 *  external services required (web_search is asserted shape-only).
 *  Usage: node scripts/test-sidecar-e2e.mjs
 */
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = fileURLToPath(new URL("../../..", import.meta.url));
const tmp = await mkdtemp(join(tmpdir(), "tsagent-e2e-"));

const child = spawn("python3", ["-u", "-m", "agent.service.local_sidecar", "--database", join(tmp, "e2e.db"), "--workspace-root", join(tmp, "ws")], {
  cwd: repoRoot,
  stdio: ["pipe", "pipe", "pipe"],
});

let buffer = "";
const pending = new Map();
child.stdout.on("data", (chunk) => {
  buffer += chunk.toString();
  let i;
  while ((i = buffer.indexOf("\n")) >= 0) {
    const line = buffer.slice(0, i).trim();
    buffer = buffer.slice(i + 1);
    if (!line) continue;
    const msg = JSON.parse(line);
    if (msg.id && pending.has(msg.id)) {
      pending.get(msg.id)(msg);
      pending.delete(msg.id);
    }
  }
});
child.stderr.on("data", (d) => process.stderr.write(`[sidecar] ${d}`));

let seq = 0;
const call = (method, params = {}) =>
  new Promise((resolve, reject) => {
    const id = `e2e-${++seq}`;
    pending.set(id, resolve);
    setTimeout(() => reject(new Error(`${method} timed out`)), 15000);
    child.stdin.write(JSON.stringify({ id, method, params }) + "\n");
  });

const ident = { tenant_id: "t-e2e", user_id: "u-e2e", session_id: "s-e2e", request_id: "r-1" };
let pass = 0, fail = 0;
const check = (name, ok, detail = "") => {
  console.log(`${ok ? "ok" : "not ok"} ${++pass + fail - (ok ? 0 : 1)} - ${name}${detail ? ` (${detail})` : ""}`);
  if (ok) pass++; else { pass--; fail++; }
};

try {
  const health = await call("health");
  check("health", health.ok === true);

  const caps = await call("list_capabilities", ident);
  const capNames = (caps.result ?? []).map((c) => c.capability);
  check("list_capabilities returns real registry", caps.ok && capNames.includes("web_search") && capNames.includes("file_read"), `${capNames.length} capabilities`);

  const skills = await call("list_skills", ident);
  check("list_skills responds", skills.ok === true && Array.isArray(skills.result));

  const schedules = await call("list_schedules", ident);
  check("list_schedules empty catalog", schedules.ok && Array.isArray(schedules.result));

  const profiles = await call("list_profiles", ident);
  check("list_profiles empty catalog", profiles.ok && Array.isArray(profiles.result));

  const sessions = await call("list_sessions", ident);
  check("list_sessions responds", sessions.ok && Array.isArray(sessions.result));

  const search = await call("web_search", { query: "e2e", max_results: 2 });
  // Provider availability varies by environment — assert the wire contract,
  // not a live result: ok:true with {provider,content} OR a provider error.
  const shape = search.ok && search.result && "provider" in search.result;
  const errShape = search.ok === false && search.error && typeof search.error.code === "string";
  check("web_search wire contract", shape || errShape, shape ? `provider=${search.result.provider}` : search.error?.code);

  const badRun = await call("subscribe_events", { ...ident, run_id: "run-missing", after_sequence: 0 });
  check("subscribe_events RUN_NOT_FOUND", badRun.ok === false && badRun.error?.code === "RUN_NOT_FOUND");

  const noParent = await call("delegate_run", { ...ident, parent_run_id: "run-missing", request_text: "sub", target_planet: "mars" });
  check("delegate_run RUN_NOT_FOUND", noParent.ok === false && noParent.error?.code === "RUN_NOT_FOUND");

  const tasks = await call("create_task", { ...ident, origin_planet: "mars" });
  check("create_task", tasks.ok && typeof tasks.result?.task_id === "string");

  const shutdown = await call("shutdown");
  check("shutdown", shutdown.ok === true || shutdown.result?.status === "shutting_down");
} finally {
  child.kill();
  await rm(tmp, { recursive: true, force: true });
}

console.log(`# ${pass + fail} checks · ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
