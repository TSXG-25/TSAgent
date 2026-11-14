import { useCallback, useEffect, useState } from "react";
import { SCENES } from "../../app/scenes";
import { WorkspaceFrame } from "../../design-system/WorkspaceFrame";
import AgentComposer from "../../app/AgentComposer";
import { requestIdForRun } from "../../app/ui";
import { t } from "../../app/i18n";
import type { RunCtx } from "../../app/runCtx";
import { WorkspaceTasks } from "../../app/WorkspaceTasks";
import { useWorkspaceCurrentRun } from "../../app/workspaceSelection";
import type { GraphRunStepSummary, HandoffSummary, ProfileSummary, RunStatus, SkillSummary, WorkflowGraphSummary } from "../../types/service";

/** Laboratory per hand-drawn spec §9 — element palette | workflow canvas
 *  (editable delegation nodes + save) | workflow list + durable child-run
 *  status. Delegation status is re-read from run_handoffs, not from the
 *  one-shot admission response. */
const ELEMS = ["read_file", "web_search", "llm", "write_file", "approval", "condition"];

/** Child runs execute in `<delegate session>:subtask:<taskId>` — mirrors
 *  SUBTASK_SESSION_MARKER in agent/service/service.py. */
function subtaskSessionId(sessionId: string, taskId: string): string {
  return `${sessionId}:subtask:${taskId}`;
}

interface DelegationRow {
  handoff: HandoffSummary;
  status: RunStatus | "";
}

export default function UranusWorkspace({ ctx }: { ctx: RunCtx }) {
  const { service } = ctx;
    const scene = SCENES[7];
  const currentRun = useWorkspaceCurrentRun(ctx, "uranus");
  const [profiles, setProfiles] = useState<ProfileSummary[]>([]);
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [graphs, setGraphs] = useState<WorkflowGraphSummary[]>([]);
  const [nodes, setNodes] = useState<string[]>([]);
  const [lastSteps, setLastSteps] = useState<GraphRunStepSummary[]>([]);
  const [delegations, setDelegations] = useState<DelegationRow[]>([]);
  const [name, setName] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const req = useCallback(() => ({ tenantId: service.identity.tenantId, sessionId: service.identity.sessionId, requestId: requestIdForRun(0) }), [service]);
  const sessionId = service.identity.sessionId;
  useEffect(() => {
    service.listProfiles(req()).then(setProfiles).catch((e) => setErr(String(e)));
    service.listSkills(req()).then(setSkills).catch((e) => setErr(String(e)));
    service.listWorkflowGraphs(req()).then(setGraphs).catch((e) => setErr(String(e)));
  }, [service, req]);

  /** 后台执行状态查询 — run_graph 接纳后立刻返回 PENDING，真实进度
   *  从持久化 run_handoffs + 各子 Run 的 head 重新读取。 */
  const refreshDelegations = useCallback(async () => {
    try {
      const handoffs = await service.listHandoffs({ ...req(), limit: 200 });
      const rows = await Promise.all(handoffs.map(async (handoff) => {
        if (!handoff.targetRunId || !handoff.targetTaskId) return { handoff, status: "" as RunStatus | "" };
        try {
          const snap = await service.getRun({
            tenantId: service.identity.tenantId,
            sessionId: subtaskSessionId(sessionId, handoff.targetTaskId),
            runId: handoff.targetRunId,
          });
          return { handoff, status: snap.status };
        } catch { return { handoff, status: "" as RunStatus | "" }; }
      }));
      setDelegations(rows);
    } catch (e) { setErr(String(e)); }
  }, [service, req, sessionId]);
  useEffect(() => { void refreshDelegations(); }, [refreshDelegations]);

  async function save() {
    if (!name.trim() || nodes.every((n) => !n.trim())) return;
    try {
      await service.upsertWorkflowGraph({
        ...req(), graphId: `g-${Date.now().toString(36)}`, name: name.trim(),
        // Canvas contract: nodes are delegated tasks, edges are {from,to}
        // success dependencies — the backend rejects anything else.
        definition: {
          nodes: nodes.filter((n) => n.trim()).map((n, i) => ({ id: `n${i}`, type: "task", request_text: n.trim() })),
          edges: nodes.filter((n) => n.trim()).slice(1).map((_, i) => ({ from: `n${i}`, to: `n${i + 1}` })),
        },
      });
      setName(""); setNodes([]);
      setGraphs(await service.listWorkflowGraphs(req()));
    } catch (e) { setErr(String(e)); }
  }

  async function launch(graphId: string) {
    if (!currentRun) return;
    try {
      const steps = await service.runGraph({ ...req(), graphId, parentRunId: currentRun.runId });
      setLastSteps(steps);
      // 接纳响应只有 PENDING — 拉一次持久化 handoff 状态。
      void refreshDelegations();
    } catch (e) { setErr(String(e)); }
  }

  return (
    <WorkspaceFrame scene={scene} ctx={ctx}>
      <div className="lab2">
        {/* element palette */}
        <aside className="lab-elems">
          <h4 className="ws-mod">{t("ura.elems")}</h4>
          {ELEMS.map((e) => (
            <button className="lab-elem" key={e} onClick={() => setNodes((n) => [...n, e])} type="button">{e}</button>
          ))}
        </aside>
        {/* workflow canvas — 节点为可编辑委派任务文本 */}
        <main className="lab-canvas">
          <div className="ws-hud"><span className="hud-mark">◈</span>
            <div className="ws-hud-info"><strong>{t("ura.canvas")}</strong>
              <small>{t("ura.nodes", { n: nodes.length })}{currentRun ? ` · ${currentRun.runId}` : ""}</small></div>
          </div>
          <div className="lab-nodes">
            {nodes.map((n, i) => (
              <div className="lab-node" key={i}>
                <span className="lab-node-id">n{i}</span>
                <input
                  className="lab-node-text"
                  onChange={(e) => setNodes((v) => v.map((x, j) => (j === i ? e.target.value : x)))}
                  value={n}
                />
                <button className="lab-node-x" onClick={() => setNodes((v) => v.filter((_, j) => j !== i))} type="button">×</button>
              </div>
            ))}
            {nodes.length === 0 && <p className="omni-empty">{t("ura.empty-nodes")}</p>}
          </div>
          <div className="lab-save">
            <input onChange={(e) => setName(e.target.value)} placeholder="name" value={name} />
            <button className="btn primary" disabled={!name.trim() || nodes.every((n) => !n.trim())} onClick={() => void save()} type="button">{t("ura.save")}</button>
          </div>
          {err && <p className="omni-empty" style={{ color: "var(--st-danger,#ff5a4f)" }}>{err}</p>}
        </main>
        {/* workflow list + delegation status + profiles */}
        <aside className="lab-side">
          <h4 className="ws-mod">{t("ura.workflows")}</h4>
          {graphs.map((g) => (
            <div className="lab-graph-row" key={g.graphId}>
              <strong>{g.name}</strong><small>{g.graphId}</small>
              <button className="lab-run" disabled={!currentRun} onClick={() => void launch(g.graphId)} title={currentRun ? t("ura.run-graph") : t("ura.need-run")} type="button">{t("ura.run")}</button>
            </div>
          ))}
          {graphs.length === 0 && <p className="omni-empty">{t("ura.no-graphs")}</p>}
          {lastSteps.length > 0 && (
            <div className="lab-steps">
              {lastSteps.map((s) => (
                <div className="lab-graph-row" key={s.nodeId}>
                  <strong>{s.nodeId}</strong>
                  <small>{s.status || "PENDING"}</small>
                  {s.runId && (
                    <button className="ven-link" onClick={() => ctx.openRun(s.runId, s.sessionId)} title={t("uranus.goto-run")} type="button">→ {s.runId}</button>
                  )}
                </div>
              ))}
            </div>
          )}
          <h4 className="ws-mod">{t("ura.delegations")}
            <button className="ven-link" onClick={() => void refreshDelegations()} title={t("ura.refresh-status")} type="button">↻</button>
          </h4>
          {delegations.map((d) => (
            <div className="lab-graph-row" key={d.handoff.handoffId}>
              <strong>{d.handoff.targetTaskId || d.handoff.sourceRunId}</strong>
              <small>{d.handoff.targetPlanet || "—"} · {d.status || t("ura.pending")}</small>
              {d.handoff.targetRunId && (
                <button className="ven-link" onClick={() => ctx.openRun(d.handoff.targetRunId, subtaskSessionId(sessionId, d.handoff.targetTaskId))} title={t("uranus.goto-run")} type="button">→ {d.handoff.targetRunId}</button>
              )}
            </div>
          ))}
          {delegations.length === 0 && <p className="omni-empty">{t("ura.no-delegations")}</p>}
          <h4 className="ws-mod">{t("ura.skills")}</h4>
          {skills.map((sk) => <div className="lab-graph-row" key={sk.skillId}><strong>{sk.name}</strong><small>{sk.description.slice(0, 40)}</small></div>)}
          <h4 className="ws-mod">{t("ura.profiles")}</h4>
          {profiles.map((p) => <div className="lab-graph-row" key={p.profileId}><strong>{p.displayName || p.profileId}</strong><small>@{p.planetAffinity} · {t("ura.n-skills", { n: p.skillRefs.length })}</small></div>)}
          <AgentComposer ctx={ctx} planet="uranus" placeholder={t("ura.composer-ph")} />
                    <WorkspaceTasks ctx={ctx} planet="uranus" />
          <div className="ws-statusbar"><span>{graphs.length} GRAPHS</span><span>{profiles.length} PROFILES</span><span className="right">uranus</span></div>
        </aside>
      </div>
    </WorkspaceFrame>
  );
}
