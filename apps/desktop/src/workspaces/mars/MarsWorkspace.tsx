import { useCallback, useEffect, useState } from "react";
import { SCENES } from "../../app/scenes";
import { WorkspaceFrame } from "../../design-system/WorkspaceFrame";
import AgentComposer from "../../app/AgentComposer";
import { requestIdForRun, StatusBadge, Icon } from "../../app/ui";
import type { RunCtx } from "../../app/runCtx";
import type { WorkspaceFileSummary } from "../../types/service";

/** Forge per hand-drawn spec §6 — mode rail | file tree | tabs+editor w/
 *  line numbers | right control rail | AgentComposer. Code mode first. */
const MODES = [
  { id: "code", icon: "file-code", label: "Code" },
  { id: "data", icon: "list", label: "Data" },
  { id: "eng", icon: "tools", label: "Eng" },
  { id: "cad", icon: "layers", label: "CAD" },
];

export default function MarsWorkspace({ ctx }: { ctx: RunCtx }) {
  const { service, activeRun } = ctx;
  const scene = SCENES[4];
  const [mode, setMode] = useState("code");
  const [files, setFiles] = useState<WorkspaceFileSummary[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [content, setContent] = useState("");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const req = useCallback(() => ({ tenantId: service.identity.tenantId, sessionId: service.identity.sessionId, requestId: requestIdForRun(0) }), [service]);
  useEffect(() => {
    service.listWorkspaceFiles(req()).then(setFiles).catch((e) => setErr(String(e)));
  }, [service, req]);

  async function openFile(path: string) {
    setSel(path); setDirty(false);
    try { const c = await service.readWorkspaceFile({ ...req(), path }); setContent(c.text); }
    catch (e) { setErr(String(e)); }
  }
  async function save() {
    if (!sel) return;
    setSaving(true);
    try { await service.writeWorkspaceFile({ ...req(), path: sel, content }); setDirty(false); }
    catch (e) { setErr(String(e)); } finally { setSaving(false); }
  }

  return (
    <WorkspaceFrame scene={scene} ctx={ctx}>
      <div className="forge">
        {/* EngineeringModeRail [H] */}
        <nav className="forge-modes">
          {MODES.map((m) => (
            <button className={`forge-mode ${mode === m.id ? "on" : ""}`} key={m.id} onClick={() => setMode(m.id)} title={m.label} type="button">
              <Icon name={m.icon as never} size={16} /><span>{m.label}</span>
            </button>
          ))}
        </nav>
        {/* ProjectFileTree */}
        <aside className="forge-tree">
          <h4 className="ws-mod">Explorer</h4>
          {files.map((f) => (
            <button className={`forge-file ${sel === f.path ? "on" : ""}`} key={f.path} onClick={() => void openFile(f.path)} type="button">
              {f.path}
            </button>
          ))}
          {err && <p className="omni-empty" style={{ color: "var(--st-danger,#ff5a4f)" }}>{err}</p>}
        </aside>
        {/* Tabs + editor */}
        <main className="forge-editor">
          <div className="ws-hud"><span className="hud-mark">◈</span>
            <div className="ws-hud-info"><strong>{sel ?? "Forge"}</strong>
              <small>{ctx.activeRun ? <code>{ctx.activeRun.runId} · {ctx.activeRun.status}</code> : "no active run"}</small></div>
          </div>
          <div className="forge-tabs">
            {sel ? <span className="forge-tab on">{sel}{dirty ? " ●" : ""}</span> : <span className="forge-tab">no file</span>}
            {sel && <button className="forge-save" disabled={!dirty || saving} onClick={() => void save()} type="button">{saving ? "…" : "Save"}</button>}
          </div>
          <div className="forge-code">
            {sel ? content.split("\n").map((l, i) => (
              <div className="forge-line" key={i}><span>{i + 1}</span><textarea className="forge-line-edit" defaultValue={l} onChange={(e) => {
                const lines = content.split("\n"); lines[i] = e.target.value; setContent(lines.join("\n")); setDirty(true);
              }} rows={1} /></div>
            )) : <div className="empty-state"><p>Select a file</p></div>}
          </div>
        </main>
        {/* EngineeringActionPanel */}
        <aside className="forge-ctl">
          {activeRun && <div className="forge-status"><StatusBadge status={activeRun.status} /><code>{activeRun.runId}</code></div>}
          <div className="forge-ctl-hint">Controls / Agent Actions</div>
          <AgentComposer ctx={ctx} planet="mars" placeholder="Task for Forge…" />
          <div className="ws-statusbar"><span className="sys-ok">● ONLINE</span><span>{files.length} FILES</span><span>{mode.toUpperCase()}</span><span className="right">mars</span></div>
        </aside>
      </div>
    </WorkspaceFrame>
  );
}
