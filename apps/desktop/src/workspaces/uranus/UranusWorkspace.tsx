import { useCallback, useEffect, useState } from "react";
import { SCENES } from "../../app/scenes";
import { WorkspaceFrame } from "../../design-system/WorkspaceFrame";
import AgentComposer from "../../app/AgentComposer";
import { requestIdForRun } from "../../app/ui";
import type { RunCtx } from "../../app/runCtx";
import type { GraphRunStepSummary, ProfileSummary, SkillSummary, WorkflowGraphSummary } from "../../types/service";

/** Laboratory per hand-drawn spec §9 — element palette | workflow canvas
 *  (nodes + save) | workflow list. Real workflow_graphs domain. */
const ELEMS = ["read_file", "web_search", "llm", "write_file", "approval", "condition"];

export default function UranusWorkspace({ ctx }: { ctx: RunCtx }) {
  const { service } = ctx;
  const scene = SCENES[7];
  const [profiles, setProfiles] = useState<ProfileSummary[]>([]);
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [graphs, setGraphs] = useState<WorkflowGraphSummary[]>([]);
  const [nodes, setNodes] = useState<string[]>([]);
  const [lastSteps, setLastSteps] = useState<GraphRunStepSummary[]>([]);
  const [name, setName] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const req = useCallback(() => ({ tenantId: service.identity.tenantId, sessionId: service.identity.sessionId, requestId: requestIdForRun(0) }), [service]);
  useEffect(() => {
    service.listProfiles(req()).then(setProfiles).catch(() => {});
    service.listSkills(req()).then(setSkills).catch(() => {});
    service.listWorkflowGraphs(req()).then(setGraphs).catch((e) => setErr(String(e)));
  }, [service, req]);

  async function save() {
    if (!name.trim() || nodes.length === 0) return;
    try {
      await service.upsertWorkflowGraph({
        ...req(), graphId: `g-${Date.now().toString(36)}`, name: name.trim(),
        // Canvas contract: nodes are delegated tasks, edges are {from,to}
        // success dependencies — the backend rejects anything else.
        definition: {
          nodes: nodes.map((n, i) => ({ id: `n${i}`, type: "task", request_text: n })),
          edges: nodes.slice(1).map((_, i) => ({ from: `n${i}`, to: `n${i + 1}` })),
        },
      });
      setName(""); setNodes([]);
      setGraphs(await service.listWorkflowGraphs(req()));
    } catch (e) { setErr(String(e)); }
  }

  return (
    <WorkspaceFrame scene={scene} ctx={ctx}>
      <div className="lab2">
        {/* element palette */}
        <aside className="lab-elems">
          <h4 className="ws-mod">elems</h4>
          {ELEMS.map((e) => (
            <button className="lab-elem" key={e} onClick={() => setNodes((n) => [...n, e])} type="button">{e}</button>
          ))}
        </aside>
        {/* workflow canvas */}
        <main className="lab-canvas">
          <div className="ws-hud"><span className="hud-mark">◈</span>
            <div className="ws-hud-info"><strong>workflow 搭建台</strong>
              <small>{nodes.length} nodes{ctx.activeRun ? ` · ${ctx.activeRun.runId}` : ""}</small></div>
          </div>
          <div className="lab-nodes">
            {nodes.map((n, i) => (
              <div className="lab-node" key={i}>
                <span className="lab-node-id">n{i}</span>
                <strong>{n}</strong>
                <button className="lab-node-x" onClick={() => setNodes((v) => v.filter((_, j) => j !== i))} type="button">×</button>
              </div>
            ))}
            {nodes.length === 0 && <p className="omni-empty">点左侧 elems 添加节点——节点按顺序连边</p>}
          </div>
          <div className="lab-save">
            <input onChange={(e) => setName(e.target.value)} placeholder="name" value={name} />
            <button className="btn primary" disabled={!name.trim() || nodes.length === 0} onClick={() => void save()} type="button">save</button>
          </div>
          {err && <p className="omni-empty" style={{ color: "var(--st-danger,#ff5a4f)" }}>{err}</p>}
        </main>
        {/* workflow list + profiles */}
        <aside className="lab-side">
          <h4 className="ws-mod">workflows</h4>
          {graphs.map((g) => (
            <div className="lab-graph-row" key={g.graphId}>
              <strong>{g.name}</strong><small>{g.graphId}</small>
              <button className="lab-run" disabled={!ctx.activeRun} onClick={() => void service.runGraph({ ...req(), graphId: g.graphId, parentRunId: ctx.activeRun!.runId }).then((steps) => setLastSteps(steps)).catch((e) => setErr(String(e)))} title={ctx.activeRun ? "Run graph (delegated child runs)" : "需要 active run 作宿主"} type="button">▶ run</button>
            </div>
          ))}
          {lastSteps.length > 0 && (
            <div className="lab-steps">
              {lastSteps.map((s) => (
                <div className="lab-graph-row" key={s.nodeId}>
                  <strong>{s.nodeId}</strong>
                  <small>{s.status || "PENDING"}{s.runId ? ` · ${s.runId}` : ""}</small>
                </div>
              ))}
            </div>
          )}
          {graphs.length === 0 && <p className="omni-empty">No saved graphs.</p>}
          <h4 className="ws-mod">skills</h4>
          {skills.map((sk) => <div className="lab-graph-row" key={sk.skillId}><strong>{sk.name}</strong><small>{sk.description.slice(0, 40)}</small></div>)}
          <h4 className="ws-mod">profiles</h4>
          {profiles.map((p) => <div className="lab-graph-row" key={p.profileId}><strong>{p.displayName || p.profileId}</strong><small>@{p.planetAffinity} · {p.skillRefs.length} skills</small></div>)}
          <AgentComposer ctx={ctx} planet="uranus" placeholder="Task for Lab…" />
          <div className="ws-statusbar"><span className="sys-ok">● ONLINE</span><span>{graphs.length} GRAPHS</span><span>{profiles.length} PROFILES</span><span className="right">uranus</span></div>
        </aside>
      </div>
    </WorkspaceFrame>
  );
}
