import { useCallback, useEffect, useRef, useState } from "react";
import { SCENES } from "../../app/scenes";
import { WorkspaceFrame } from "../../design-system/WorkspaceFrame";
import AgentComposer from "../../app/AgentComposer";
import { requestIdForRun } from "../../app/ui";
import { t } from "../../app/i18n";
import type { RunCtx } from "../../app/runCtx";
import { WorkspaceTasks } from "../../app/WorkspaceTasks";
import { useWorkspaceCurrentRun } from "../../app/workspaceSelection";
import type { GraphExecutionRef, GraphExecutionSummary, ProfileSummary, SkillSummary, WorkflowGraphSummary } from "../../types/service";

/** Laboratory per hand-drawn spec §9 — element palette | workflow canvas
 *  (editable delegation nodes + save) | workflow list + durable graph
 *  execution status. Execution state comes from the persisted
 *  node→run/session rows, not from the one-shot PENDING admission
 *  response or session-wide handoff listing. */
const ELEMS = ["read_file", "web_search", "llm", "write_file", "approval", "condition"];

const TERMINAL = /^(COMPLETED|FAILED|CANCELLED|TIMED_OUT|SKIPPED|INTERRUPTED)/;

export default function UranusWorkspace({ ctx }: { ctx: RunCtx }) {
  const { service } = ctx;
    const scene = SCENES[7];
  const currentRun = useWorkspaceCurrentRun(ctx, "uranus");
  const [profiles, setProfiles] = useState<ProfileSummary[]>([]);
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [graphs, setGraphs] = useState<WorkflowGraphSummary[]>([]);
  const [nodes, setNodes] = useState<string[]>([]);
  const [executions, setExecutions] = useState<GraphExecutionRef[]>([]);
  const [execSel, setExecSel] = useState<string | null>(null);
  const [execution, setExecution] = useState<GraphExecutionSummary | null>(null);
  const [name, setName] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const req = useCallback(() => ({ tenantId: service.identity.tenantId, sessionId: service.identity.sessionId, requestId: requestIdForRun(0) }), [service]);
  const sessionId = service.identity.sessionId;
  /** Response gate — an in-flight getGraphExecution for execution A must
   *  not commit after B is selected or the session moved (a late A would
   *  overwrite B's detail, and a terminal A would stop B's polling). */
  const execToken = useRef<{ id: string; sessionId: string } | null>(null);
  useEffect(() => {
    setExecutions([]);
    setExecution(null);
    setExecSel(null);
    execToken.current = null;
    service.listProfiles(req()).then(setProfiles).catch((e) => setErr(String(e)));
    service.listSkills(req()).then(setSkills).catch((e) => setErr(String(e)));
    service.listWorkflowGraphs(req()).then(setGraphs).catch((e) => setErr(String(e)));
    service.listGraphExecutions(req()).then(setExecutions).catch((e) => setErr(String(e)));
  }, [service, req, sessionId]);

  /** 执行状态由持久化节点行投影 — 可重复查询，重启后仍为真实态。 */
  const refreshExecution = useCallback(async (executionId: string) => {
    const token = { id: executionId, sessionId: service.identity.sessionId };
    execToken.current = token;
    try {
      const summary = await service.getGraphExecution({ ...req(), executionId });
      if (execToken.current === token) setExecution(summary);
      return summary;
    } catch (e) {
      if (execToken.current === token) setErr(String(e));
      return null;
    }
  }, [service, req]);

  const selectExecution = useCallback((id: string) => {
    setExecSel(id);
    void refreshExecution(id);
  }, [refreshExecution]);

  // 未终态的执行自动轮询 — 终态后停止。
  useEffect(() => {
    if (!execSel || !execution) return;
    if (execution.steps.every((s) => TERMINAL.test(s.status ?? ""))) return;
    const timer = globalThis.setInterval(() => { void refreshExecution(execSel); }, 2000);
    return () => globalThis.clearInterval(timer);
  }, [execSel, execution, refreshExecution]);

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
      const executionId = steps[0]?.executionId;
      setExecutions(await service.listGraphExecutions(req()));
      if (executionId) selectExecution(executionId);
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
        {/* workflow list + execution status + profiles */}
        <aside className="lab-side">
          <h4 className="ws-mod">{t("ura.workflows")}</h4>
          {graphs.map((g) => (
            <div className="lab-graph-row" key={g.graphId}>
              <strong>{g.name}</strong><small>{g.graphId}</small>
              <button className="lab-run" disabled={!currentRun} onClick={() => void launch(g.graphId)} title={currentRun ? t("ura.run-graph") : t("ura.need-run")} type="button">{t("ura.run")}</button>
            </div>
          ))}
          {graphs.length === 0 && <p className="omni-empty">{t("ura.no-graphs")}</p>}
          <h4 className="ws-mod">{t("ura.delegations")}
            <button className="ven-link" onClick={() => { if (execSel) void refreshExecution(execSel); }} title={t("ura.refresh-status")} type="button">↻</button>
          </h4>
          {executions.length > 0 && (
            <select className="lab-execpick" onChange={(e) => { if (e.target.value) selectExecution(e.target.value); }} value={execSel ?? ""}>
              <option value="">{t("ura.pick-exec")}</option>
              {executions.map((x) => (
                <option key={x.executionId} value={x.executionId}>{x.graphId} · {x.executionId.split(":").pop()}</option>
              ))}
            </select>
          )}
          {execution && execution.steps.map((s) => (
            <div className="lab-graph-row" key={s.nodeId}>
              <strong>{s.nodeId}</strong>
              <small>{s.status || "PENDING"}{s.detail ? ` — ${s.detail}` : ""}</small>
              {s.runId && (
                <button className="ven-link" onClick={() => ctx.openRun(s.runId, s.sessionId || undefined)} title={t("uranus.goto-run")} type="button">→ {s.runId}</button>
              )}
            </div>
          ))}
          {!execution && executions.length === 0 && <p className="omni-empty">{t("ura.no-delegations")}</p>}
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
