import { useEffect, useMemo, useState } from "react";
import { SCENES } from "../../app/scenes";
import { WorkspaceFrame } from "../../design-system/WorkspaceFrame";
import AgentComposer from "../../app/AgentComposer";
import { requestIdForRun, StatusBadge, Icon } from "../../app/ui";
import type { RunCtx } from "../../app/runCtx";
import type { WorkspaceFileSummary } from "../../types/service";
import { ForgeDocumentSession } from "./documentSession";

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
  const [err, setErr] = useState<string | null>(null);
  const [, bump] = useState(0);
  // The session binds the document (path + workspace identity + baseline +
  // draft); it survives selection churn and rejects writes when the bound
  // workspace or baseline no longer matches.
  const session = useMemo(
    () =>
      new ForgeDocumentSession(
        service,
        () => ({ tenantId: service.identity.tenantId, sessionId: service.identity.sessionId }),
        () => bump((v) => v + 1),
      ),
    [service],
  );
  const doc = session.doc;

  useEffect(() => {
    const identity = service.identity;
    service
      .listWorkspaceFiles({
        tenantId: identity.tenantId,
        sessionId: identity.sessionId,
        requestId: requestIdForRun(0),
      })
      .then(setFiles)
      .catch((e) => setErr(String(e)));
  }, [service]);

  async function openFile(path: string) {
    setErr(null);
    try {
      if (!(await session.open(path))) {
        if (!window.confirm(`Discard unsaved changes to ${session.doc?.path ?? "current file"}?`)) return;
        await session.open(path, { discardDraft: true });
      }
    } catch (e) {
      setErr(String(e));
    }
  }

  async function save() {
    setErr(null);
    try { await session.save(); }
    catch (e) { setErr(String(e)); }
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
            <button className={`forge-file ${doc?.path === f.path ? "on" : ""}`} key={f.path} onClick={() => void openFile(f.path)} type="button">
              {f.path}
            </button>
          ))}
          {err && <p className="omni-empty" style={{ color: "var(--st-danger,#ff5a4f)" }}>{err}</p>}
        </aside>
        {/* Tabs + editor */}
        <main className="forge-editor">
          <div className="ws-hud"><span className="hud-mark">◈</span>
            <div className="ws-hud-info"><strong>{doc?.path ?? "Forge"}</strong>
              <small>{ctx.activeRun ? <code>{ctx.activeRun.runId} · {ctx.activeRun.status}</code> : "no active run"}</small></div>
          </div>
          <div className="forge-tabs">
            {doc ? (
              <span className="forge-tab on">
                {doc.path}{session.dirty ? " ●" : ""}{doc.redacted ? " · read-only" : ""}
              </span>
            ) : <span className="forge-tab">no file</span>}
            {doc && !doc.redacted && (
              <button className="forge-save" disabled={!session.dirty || session.saving} onClick={() => void save()} type="button">
                {session.saving ? "…" : "Save"}
              </button>
            )}
          </div>
          <div className="forge-code">
            {doc ? session.content.split("\n").map((l, i) => (
              <div className="forge-line" key={`${doc.path}:${i}`}><span>{i + 1}</span><textarea className="forge-line-edit" value={l} readOnly={doc.redacted} onChange={(e) => {
                const lines = session.content.split("\n"); lines[i] = e.target.value; session.edit(lines.join("\n"));
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
