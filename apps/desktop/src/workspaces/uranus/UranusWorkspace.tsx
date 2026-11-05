import { useCallback, useEffect, useMemo, useState } from "react";
import { SCENES } from "../../app/scenes";
import { WorkspaceFrame } from "../../design-system/WorkspaceFrame";
import { requestIdForRun } from "../../app/ui";
import type { RunCtx } from "../../app/runCtx";
import type { CapabilitySummary, GraphRunStepSummary, ProfileSummary, SkillSummary, WorkflowGraphSummary } from "../../types/service";

/** §5.8 Uranus / Laboratory — library | L→R binding graph (snap grid is the
 *  only functional grid in the system) | inspector with checkbox skills. */
const GLYPH: Record<string, string> = { mercury: "☿", venus: "♀", earth: "⊕", mars: "♂", jupiter: "♃", saturn: "♄", uranus: "⛢", neptune: "♆", pluto: "♇" };

export default function UranusWorkspace({ ctx }: { ctx: RunCtx }) {
  const { service } = ctx;
  const scene = SCENES[7];
  const [profiles, setProfiles] = useState<ProfileSummary[]>([]);
  const [capabilities, setCapabilities] = useState<CapabilitySummary[]>([]);
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [editing, setEditing] = useState<ProfileSummary | null>(null);
  const [libQ, setLibQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [c, s, p] = await Promise.all([
        service.listCapabilities({ tenantId: service.identity.tenantId, sessionId: service.identity.sessionId, requestId: requestIdForRun(0) } as never),
        service.listSkills({ tenantId: service.identity.tenantId, sessionId: service.identity.sessionId, requestId: requestIdForRun(0) } as never),
        service.listProfiles({ tenantId: service.identity.tenantId, sessionId: service.identity.sessionId, requestId: requestIdForRun(0) } as never),
      ]);
      setCapabilities(c); setSkills(s); setProfiles(p); setError(null);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }, [service]);
  useEffect(() => { void refresh(); }, [refresh]);

  async function save(p: ProfileSummary) {
    setBusy(true);
    try {
      await service.upsertProfile({
        tenantId: service.identity.tenantId, sessionId: service.identity.sessionId,
        requestId: requestIdForRun(profiles.length + 1),
        profileId: p.profileId, displayName: p.displayName, planetAffinity: p.planetAffinity,
        modelRef: p.modelRef, skillRefs: p.skillRefs, permissionPolicy: p.permissionPolicy,
      } as never);
      setEditing(null); await refresh();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  const draft: ProfileSummary = editing ?? {
    profileId: `profile-${Date.now().toString(16)}`, displayName: "", planetAffinity: "mars",
    modelRef: "deepseek-chat", skillRefs: [], permissionPolicy: {}, updatedAt: "",
  };
  const boundSkills = useMemo(() => skills.filter((s) => draft.skillRefs.includes(s.skillId) || draft.skillRefs.includes(s.name)), [skills, draft.skillRefs]);
  const unknownRefs = useMemo(() => draft.skillRefs.filter((r) => !skills.some((s) => s.skillId === r || s.name === r)), [skills, draft.skillRefs]);
  const filterLib = (name: string) => !libQ || name.toLowerCase().includes(libQ.toLowerCase());

  /* workflow graph canvas — durable node-graph definitions that run as real
     delegated child runs via run_graph (C+ semantics). */
  const [graphs, setGraphs] = useState<WorkflowGraphSummary[]>([]);
  const [graphDraft, setGraphDraft] = useState<{ id: string; name: string; json: string } | null>(null);
  const [graphSteps, setGraphSteps] = useState<GraphRunStepSummary[] | null>(null);
  const [graphBusy, setGraphBusy] = useState(false);

  const refreshGraphs = useCallback(async () => {
    try {
      setGraphs(await service.listWorkflowGraphs({ tenantId: service.identity.tenantId, sessionId: service.identity.sessionId, requestId: requestIdForRun(0) } as never));
    } catch { /* older sidecar without the wire — graphs stay empty */ }
  }, [service]);
  useEffect(() => { void refreshGraphs(); }, [refreshGraphs]);

  async function saveGraph() {
    if (!graphDraft) return;
    setGraphBusy(true);
    try {
      const definition = JSON.parse(graphDraft.json) as Record<string, unknown>;
      await service.upsertWorkflowGraph({
        tenantId: service.identity.tenantId, sessionId: service.identity.sessionId,
        requestId: requestIdForRun(graphs.length + 1), graphId: graphDraft.id,
        name: graphDraft.name, definition, owner: service.identity.userId,
      } as never);
      setGraphDraft(null); await refreshGraphs();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setGraphBusy(false); }
  }

  async function runGraph(graphId: string) {
    if (!ctx.activeRun) { setError("没有活动 Run —— 先在 Sun 起个 run 再跑图。"); return; }
    setGraphBusy(true); setGraphSteps(null);
    try {
      const steps = await service.runGraph({
        tenantId: service.identity.tenantId, sessionId: service.identity.sessionId,
        requestId: requestIdForRun(graphs.length + 10), graphId,
        parentRunId: ctx.activeRun.runId,
      } as never);
      setGraphSteps(steps);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setGraphBusy(false); }
  }

  return (
    <WorkspaceFrame scene={scene} ctx={ctx}>
      <div className="ura-page">
        {/* library */}
        <aside className="ura-lib">
          <input className="sun-search" value={libQ} onChange={(e) => setLibQ(e.target.value)} placeholder="⌕ 库…" />
          <h4>Profiles</h4>
          {profiles.filter((p) => filterLib(p.displayName || p.profileId)).map((p) => (
            <button className={`ura-lib-row ${editing?.profileId === p.profileId ? "on" : ""}`} key={p.profileId} onClick={() => setEditing(p)} type="button">
              <strong>{p.displayName || p.profileId}</strong>
              <small>@{p.planetAffinity || "any"} · {p.skillRefs.length} 技能</small>
            </button>
          ))}
          <h4>Skills</h4>
          {skills.filter((s) => filterLib(s.name || s.skillId)).map((s) => (
            <div className="ura-lib-row" key={s.skillId}><strong>{s.name || s.skillId}</strong><small>{s.description?.slice(0, 60)}</small></div>
          ))}
          <h4>能力</h4>
          {capabilities.filter((c) => filterLib(c.capability)).map((c) => (
            <div className="ura-lib-row" key={c.capability}><strong>{c.capability}</strong><small>{c.tools.length} tools</small></div>
          ))}
        </aside>

        {/* binding graph: model → profile → skills → @planet */}
        <section className="ura-canvas">
          <div className="ura-flow">
            <div className="ura-node"><span>模型</span><strong>{draft.modelRef || "default"}</strong></div>
            <span className="ura-edge">──▶</span>
            <div className="ura-node core"><span>Profile</span><strong>{draft.displayName || draft.profileId}</strong><small>{boundSkills.length} 技能 · {unknownRefs.length === 0 ? "绑定有效" : `${unknownRefs.length} 无效`}</small></div>
            <span className="ura-edge">──▶</span>
            <div className="ura-col">
              {boundSkills.length > 0 ? boundSkills.map((s) => (
                <div className="ura-node mini" key={s.skillId}><strong>{s.name || s.skillId}</strong></div>
              )) : <div className="ura-node mini ghost">无技能绑定</div>}
            </div>
            <span className="ura-edge">──▶</span>
            <div className="ura-node planet"><span>{GLYPH[draft.planetAffinity] ?? "◎"}</span><strong>@{draft.planetAffinity || "any"}</strong></div>
          </div>

          {/* workflow graphs — durable node-graph definitions, run_graph = real delegated runs */}
          <div className="ura-graphs">
            <div className="ura-graphs-head">
              <h4>Workflow 图</h4>
              <button className="btn ghost" onClick={() => setGraphDraft({ id: `g-${Date.now().toString(16)}`, name: "", json: '{\n  "schema_version": 1,\n  "nodes": [\n    { "id": "n1", "type": "task", "request_text": "", "target_planet": "mercury" }\n  ],\n  "edges": []\n}' })} type="button">+ 新图</button>
            </div>
            {graphs.map((g) => (
              <div className="ura-graph-row" key={g.graphId}>
                <span className="ura-graph-meta">
                  <strong>{g.name || g.graphId}</strong>
                  <small>{g.graphId} · {JSON.parse(g.definitionJson || "{}").nodes?.length ?? 0} 节点</small>
                </span>
                <button className="btn ghost" onClick={() => setGraphDraft({ id: g.graphId, name: g.name, json: JSON.stringify(JSON.parse(g.definitionJson || "{}"), null, 2) })} type="button">编辑</button>
                <button className="btn primary" disabled={graphBusy} onClick={() => void runGraph(g.graphId)} type="button">▶ 执行</button>
              </div>
            ))}
            {graphs.length === 0 && !graphDraft && <p className="omni-empty">还没有持久化的图。</p>}

            {graphDraft && (
              <div className="ura-graph-editor">
                <div className="field"><label>图 ID</label><input value={graphDraft.id} onChange={(e) => setGraphDraft({ ...graphDraft, id: e.target.value })} /></div>
                <div className="field"><label>名称</label><input value={graphDraft.name} onChange={(e) => setGraphDraft({ ...graphDraft, name: e.target.value })} /></div>
                <div className="field"><label>定义（nodes / edges）</label>
                  <textarea className="ura-graph-json" rows={10} spellCheck={false} value={graphDraft.json} onChange={(e) => setGraphDraft({ ...graphDraft, json: e.target.value })} />
                </div>
                <div className="ura-graph-actions">
                  <button className="btn primary" disabled={graphBusy} onClick={() => void saveGraph()} type="button">{graphBusy ? "保存中…" : "保存"}</button>
                  <button className="btn ghost" onClick={() => setGraphDraft(null)} type="button">取消</button>
                </div>
              </div>
            )}

            {graphSteps && (
              <div className="ura-graph-steps">
                <h4>执行步骤</h4>
                {graphSteps.map((s) => (
                  <div className="ura-step" key={s.nodeId}>
                    <span className="task-chip">{s.nodeId}</span>
                    <code>{s.runId}</code>
                    <small>{s.sessionId}</small>
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>

        {/* inspector */}
        <aside className="ura-inspector">
          <div className="field"><label>名称</label>
            <input value={draft.displayName} onChange={(e) => setEditing({ ...draft, displayName: e.target.value })} /></div>
          <div className="field"><label>模型</label>
            <select value={draft.modelRef} onChange={(e) => setEditing({ ...draft, modelRef: e.target.value })}>
              <option value="deepseek-chat">deepseek-chat</option><option value="deepseek-reasoner">deepseek-reasoner</option>
            </select></div>
          <div className="field"><label>星球</label>
            <select value={draft.planetAffinity} onChange={(e) => setEditing({ ...draft, planetAffinity: e.target.value })}>
              {SCENES.filter((s) => s.id !== "sun").map((s) => <option key={s.id} value={s.id}>{s.workspace}</option>)}
            </select></div>
          <div className="field"><label>技能</label>
            <div className="ura-skill-list">
              {skills.map((s) => {
                const id = s.skillId || s.name;
                const on = draft.skillRefs.includes(s.skillId) || draft.skillRefs.includes(s.name);
                return (
                  <label className="sat-check" key={id}>
                    <input className="styled" checked={on} onChange={(e) => {
                      const refs = new Set(draft.skillRefs);
                      if (e.target.checked) refs.add(id); else refs.delete(id);
                      setEditing({ ...draft, skillRefs: [...refs] });
                    }} type="checkbox" />
                    <span>{s.name || s.skillId}</span>
                  </label>
                );
              })}
              {skills.length === 0 && <span className="omni-empty">No skills registered.</span>}
            </div>
          </div>
          <p className={unknownRefs.length === 0 ? "ura-valid" : "ura-invalid"}>
            {unknownRefs.length === 0 ? "✓ 绑定有效" : `✕ 引用了不存在的技能: ${unknownRefs.join(", ")}`}
          </p>
          <button className="btn primary" disabled={busy || unknownRefs.length > 0} onClick={() => void save(draft)} type="button">
            {busy ? "保存中…" : "保存 Profile"}
          </button>
          {error && <p className="omni-empty" style={{ color: "var(--st-danger, #ff5a4f)" }}>{error}</p>}
        </aside>
      </div>
    </WorkspaceFrame>
  );
}
