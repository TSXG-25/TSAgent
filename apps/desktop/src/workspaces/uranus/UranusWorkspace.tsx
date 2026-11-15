import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { SCENES, PLANET_SCENES } from "../../app/scenes";
import { WorkspaceFrame } from "../../design-system/WorkspaceFrame";
import AgentComposer from "../../app/AgentComposer";
import { requestIdForRun } from "../../app/ui";
import { t } from "../../app/i18n";
import type { RunCtx } from "../../app/runCtx";
import { WorkspaceTasks } from "../../app/WorkspaceTasks";
import { useWorkspaceCurrentRun } from "../../app/workspaceSelection";
import type { GraphExecutionRef, GraphExecutionSummary, ProfileSummary, SkillSummary, WorkflowGraphSummary } from "../../types/service";
import { UranusCanvas } from "./UranusCanvas";
import {
  DraftSaveGate,
  emptyCanvas,
  nextNodeId,
  overlayStepsFor,
  parseGraphDefinition,
  serializeGraphDefinition,
  type CanvasGraph,
  type CanvasNodeData,
} from "./graphModel";

/** Laboratory — Dify-style workflow studio. Center: editable DAG canvas
 *  (nodes are delegated task runs, edges are success dependencies).
 *  Left: saved graphs + palette. Right: selected node config. Bottom:
 *  picked execution's per-node status from durable execution rows.
 *  Edges carry no data — upstream output is passed only via an explicit
 *  {artifacts:<nodeId>} reference inside a node's task text. */
const TERMINAL = /^(COMPLETED|FAILED|CANCELLED|TIMED_OUT|SKIPPED|INTERRUPTED)/;

export default function UranusWorkspace({ ctx }: { ctx: RunCtx }) {
  const { service } = ctx;
  const scene = SCENES[7];
  const currentRun = useWorkspaceCurrentRun(ctx, "uranus");
  const [profiles, setProfiles] = useState<ProfileSummary[]>([]);
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [graphs, setGraphs] = useState<WorkflowGraphSummary[]>([]);
  const [executions, setExecutions] = useState<GraphExecutionRef[]>([]);
  const [execSel, setExecSel] = useState<string | null>(null);
  const [execution, setExecution] = useState<GraphExecutionSummary | null>(null);
  const [canvas, setCanvas] = useState<CanvasGraph>(emptyCanvas);
  const [openGraphId, setOpenGraphId] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [selNode, setSelNode] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const req = useCallback(() => ({ tenantId: service.identity.tenantId, sessionId: service.identity.sessionId, requestId: requestIdForRun(0) }), [service]);
  const sessionId = service.identity.sessionId;
  /** Response gate — an in-flight getGraphExecution for execution A must
   *  not commit after B is selected or the session moved. */
  const execToken = useRef<{ id: string; sessionId: string } | null>(null);
  useEffect(() => {
    setExecutions([]);
    setExecution(null);
    setExecSel(null);
    execToken.current = null;
    overlayRef.current = null;
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

  /** 保存回执票据 — 提交时的文档身份+编辑版本；迟到的响应不能
   *  把之后的修改误标已保存、也不能把 openGraphId 改回旧图。 */
  const saveGateRef = useRef<DraftSaveGate | null>(null);
  if (!saveGateRef.current) saveGateRef.current = new DraftSaveGate();
  const saveGate = saveGateRef.current;

  /** 叠加授权仅来自本端发起执行时提交的定义 — 历史执行选择不能证明
   *  当前画布与其执行定义一致，只在底部展示详情。 */
  const overlayRef = useRef<{ execId: string; definition: string } | null>(null);
  /** 响应身份核对 — A 的旧摘要在选中 B 后不得再展示。 */
  const shownExecution = execution && execution.executionId === execSel ? execution : null;
  const statusByNode = useMemo(
    () => overlayStepsFor({
      execution: shownExecution,
      execSel,
      bound: overlayRef.current,
      canvas,
    }),
    [shownExecution, execSel, canvas],
  );

  const selectExecution = useCallback((id: string) => {
    setExecSel(id);
    void refreshExecution(id);
  }, [refreshExecution]);

  // 未终态的执行自动轮询 — 终态后停止。
  useEffect(() => {
    if (!execSel || !shownExecution) return;
    if (shownExecution.steps.every((s) => TERMINAL.test(s.status ?? ""))) return;
    const timer = globalThis.setInterval(() => { void refreshExecution(execSel); }, 2000);
    return () => globalThis.clearInterval(timer);
  }, [execSel, shownExecution, refreshExecution]);

  const selected = canvas.nodes.find((n) => n.id === selNode) ?? null;

  const mutate = useCallback((next: CanvasGraph) => {
    saveGate.touch();
    setCanvas(next);
    setDirty(true);
  }, [saveGate]);

  const addNode = useCallback((type: "task" | "input") => {
    const id = nextNodeId(canvas.nodes);
    const node: CanvasNodeData = {
      id, type, requestText: "", profileId: "", targetPlanet: "",
      name: type === "input" ? `param${canvas.nodes.filter((n) => n.type === "input").length + 1}` : "",
      value: "", x: 80 + canvas.nodes.length * 40, y: 80 + canvas.nodes.length * 40,
      extra: {},
    };
    mutate({ ...canvas, nodes: [...canvas.nodes, node] });
    setSelNode(id);
  }, [canvas, mutate]);

  const patchSelected = useCallback((patch: Partial<CanvasNodeData>) => {
    if (!selNode) return;
    mutate({
      ...canvas,
      nodes: canvas.nodes.map((n) => (n.id === selNode ? { ...n, ...patch } : n)),
    });
  }, [canvas, mutate, selNode]);

  const removeSelected = useCallback(() => {
    if (!selNode) return;
    mutate({
      nodes: canvas.nodes.filter((n) => n.id !== selNode),
      edges: canvas.edges.filter((e) => e.from !== selNode && e.to !== selNode),
    });
    setSelNode(null);
  }, [canvas, mutate, selNode]);

  function clearExecSelection() {
    execToken.current = null;
    overlayRef.current = null;
    setExecSel(null);
    setExecution(null);
  }

  function newGraph() {
    saveGate.touch();
    setCanvas(emptyCanvas());
    setOpenGraphId(null);
    setName("");
    setDirty(false);
    setSelNode(null);
    clearExecSelection();
  }

  function openGraph(g: WorkflowGraphSummary) {
    saveGate.touch();
    setCanvas(parseGraphDefinition(g.definitionJson));
    setOpenGraphId(g.graphId);
    setName(g.name);
    setDirty(false);
    setSelNode(null);
    clearExecSelection();
  }

  async function save(saveAs: boolean) {
    if (!name.trim() || !canvas.nodes.some((n) => n.type === "task" && n.requestText.trim())) return;
    const ticket = saveGate.submit(openGraphId);
    try {
      const graphId = !saveAs && openGraphId ? openGraphId : `g-${Date.now().toString(36)}`;
      const saved = await service.upsertWorkflowGraph({
        ...req(), graphId, name: name.trim(),
        definition: serializeGraphDefinition(canvas),
      });
      setGraphs(await service.listWorkflowGraphs(req()));
      // 回执仅当草稿自提交后未再编辑、且仍打开同一图时才提交身份变更。
      if (saveGate.accept(ticket, openGraphId)) {
        setOpenGraphId(saved.graphId || graphId);
        setDirty(false);
      }
    } catch (e) { setErr(String(e)); }
  }

  async function launch() {
    if (!currentRun || !openGraphId || dirty) return;
    try {
      const steps = await service.runGraph({ ...req(), graphId: openGraphId, parentRunId: currentRun.runId });
      const executionId = steps[0]?.executionId;
      setExecutions(await service.listGraphExecutions(req()));
      if (executionId) {
        // 叠加授权绑定到本次提交的定义 — 唯一可证一致的来源。
        overlayRef.current = {
          execId: executionId,
          definition: JSON.stringify(serializeGraphDefinition(canvas)),
        };
        selectExecution(executionId);
      }
    } catch (e) { setErr(String(e)); }
  }

  return (
    <WorkspaceFrame scene={scene} ctx={ctx}>
      <div className="lab2">
        {/* 左 — 已保存流程 + 元素面板 */}
        <aside className="lab-elems">
          <h4 className="ws-mod">{t("ura.elems")}</h4>
          <button className="lab-elem" onClick={() => addNode("task")} type="button">+ {t("ura.add-task")}</button>
          <button className="lab-elem" onClick={() => addNode("input")} type="button">+ {t("ura.add-input")}</button>
          <h4 className="ws-mod">{t("ura.workflows")}
            <button className="ven-link" onClick={newGraph} title={t("ura.new")} type="button">＋</button>
          </h4>
          {graphs.map((g) => (
            <button
              className={`lab-graph-row lab-graph-open${g.graphId === openGraphId ? " open" : ""}`}
              key={g.graphId}
              onClick={() => openGraph(g)}
              type="button"
            >
              <strong>{g.name}</strong><small>{g.graphId}</small>
            </button>
          ))}
          {graphs.length === 0 && <p className="omni-empty">{t("ura.no-graphs")}</p>}
        </aside>
        {/* 中 — 顶栏 + DAG 画布 + 底部执行条 */}
        <main className="lab-canvas">
          <div className="lab-top">
            <input className="lab-name" onChange={(e) => { saveGate.touch(); setName(e.target.value); setDirty(true); }} placeholder={t("ura.name-ph")} value={name} />
            {dirty && <span className="ura-dirty" title={t("ura.dirty-hint")}>● {t("ura.dirty")}</span>}
            <button className="btn primary" disabled={!name.trim() || !canvas.nodes.some((n) => n.type === "task" && n.requestText.trim())} onClick={() => void save(false)} type="button">{t("ura.save")}</button>
            <button className="btn" disabled={!name.trim() || !canvas.nodes.length} onClick={() => void save(true)} title={t("ura.save-as-hint")} type="button">{t("ura.save-as")}</button>
            <button
              className="btn primary"
              disabled={!currentRun || !openGraphId || dirty}
              onClick={() => void launch()}
              title={!currentRun ? t("ura.need-run") : dirty || !openGraphId ? t("ura.save-first") : t("ura.run-graph")}
              type="button"
            >{t("ura.run")}</button>
          </div>
          <div className="lab-flow">
            {canvas.nodes.length === 0
              ? <p className="omni-empty ura-empty">{t("ura.empty-nodes")}</p>
              : (
                <UranusCanvas
                  graph={canvas}
                  onChange={mutate}
                  onSelect={setSelNode}
                  selectedId={selNode}
                  statusByNode={statusByNode}
                />
              )}
          </div>
          <div className="lab-exec">
            <div className="lab-exec-head">
              <h4 className="ws-mod">{t("ura.delegations")}</h4>
              <select className="lab-execpick" onChange={(e) => { if (e.target.value) selectExecution(e.target.value); }} value={execSel ?? ""}>
                <option value="">{t("ura.pick-exec")}</option>
                {executions.map((x) => (
                  <option key={x.executionId} value={x.executionId}>{x.graphId} · {x.executionId.split(":").pop()}</option>
                ))}
              </select>
              <button className="ven-link" onClick={() => { if (execSel) void refreshExecution(execSel); }} title={t("ura.refresh-status")} type="button">↻</button>
            </div>
            {shownExecution && (
              <div className="lab-exec-steps">
                {shownExecution.steps.map((s) => (
                  <div className="lab-graph-row" key={s.nodeId}>
                    <strong>{s.nodeId} <span className={`ura-badge ${statusClass(s.status ?? "")}`}>{s.status || "PENDING"}</span></strong>
                    {s.detail && <small>{s.detail}</small>}
                    {s.runId && (
                      <button className="ven-link" onClick={() => ctx.openRun(s.runId, s.sessionId || undefined)} title={t("uranus.goto-run")} type="button">→ {s.runId}</button>
                    )}
                  </div>
                ))}
              </div>
            )}
            {!execution && executions.length === 0 && <p className="omni-empty">{t("ura.no-delegations")}</p>}
          </div>
        </main>
        {/* 右 — 选中节点配置 + 画像/技能 + 任务区 */}
        <aside className="lab-side">
          <h4 className="ws-mod">{t("ura.node-config")}</h4>
          {selected ? (
            <div className="ura-cfg">
              <label className="ura-cfg-label">{t("ura.node-id")}<code>{selected.id}</code></label>
              {selected.type === "input" ? (
                <>
                  <label className="ura-cfg-label">{t("ura.node-name")}
                    <input onChange={(e) => patchSelected({ name: e.target.value })} value={selected.name} />
                  </label>
                  <label className="ura-cfg-label">{t("ura.node-value")}
                    <input onChange={(e) => patchSelected({ value: e.target.value })} value={selected.value} />
                  </label>
                  <small className="ura-cfg-hint">{t("ura.input-hint", { name: selected.name || "name" })}</small>
                </>
              ) : (
                <>
                  <label className="ura-cfg-label">{t("ura.node-text")}
                    <textarea
                      onChange={(e) => patchSelected({ requestText: e.target.value })}
                      rows={4}
                      value={selected.requestText}
                    />
                  </label>
                  <small className="ura-cfg-hint">{t("ura.artifacts-hint")}</small>
                  <label className="ura-cfg-label">{t("ura.node-profile")}
                    <select onChange={(e) => patchSelected({ profileId: e.target.value })} value={selected.profileId}>
                      <option value="">{t("ura.profile-default")}</option>
                      {profiles.map((p) => (
                        <option key={p.profileId} value={p.profileId}>{p.displayName || p.profileId}</option>
                      ))}
                    </select>
                  </label>
                  <label className="ura-cfg-label">{t("ura.node-target")}
                    <select onChange={(e) => patchSelected({ targetPlanet: e.target.value })} value={selected.targetPlanet}>
                      <option value="">{t("ura.target-default")}</option>
                      {PLANET_SCENES.map((s) => (
                        <option key={s.planet} value={s.planet}>{s.name} · {s.workspace}</option>
                      ))}
                    </select>
                  </label>
                  <small className="ura-cfg-hint">{t("ura.target-hint")}</small>
                </>
              )}
              <button className="btn ura-del" onClick={removeSelected} type="button">{t("ura.delete-node")}</button>
            </div>
          ) : <p className="omni-empty">{t("ura.no-selection")}</p>}
          <h4 className="ws-mod">{t("ura.profiles")}</h4>
          {profiles.map((p) => <div className="lab-graph-row" key={p.profileId}><strong>{p.displayName || p.profileId}</strong><small>@{p.planetAffinity} · {t("ura.n-skills", { n: p.skillRefs.length })}</small></div>)}
          <h4 className="ws-mod">{t("ura.skills")}</h4>
          {skills.map((sk) => <div className="lab-graph-row" key={sk.skillId}><strong>{sk.name}</strong><small>{sk.description.slice(0, 40)}</small></div>)}
          {err && <p className="omni-empty" style={{ color: "var(--st-danger,#ff5a4f)" }}>{err}</p>}
          <AgentComposer ctx={ctx} planet="uranus" placeholder={t("ura.composer-ph")} />
          <WorkspaceTasks ctx={ctx} planet="uranus" />
          <div className="ws-statusbar"><span>{graphs.length} GRAPHS</span><span>{profiles.length} PROFILES</span><span className="right">uranus</span></div>
        </aside>
      </div>
    </WorkspaceFrame>
  );
}

function statusClass(status: string): string {
  if (status === "COMPLETED") return "ok";
  if (status.startsWith("FAILED") || status === "INTERRUPTED" || status === "CANCELLED" || status === "TIMED_OUT") return "bad";
  if (status === "SKIPPED") return "skip";
  if (status === "RUNNING") return "run";
  return "wait";
}
