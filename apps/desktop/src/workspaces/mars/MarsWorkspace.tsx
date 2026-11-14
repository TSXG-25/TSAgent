import { useEffect, useMemo, useState } from "react";
import { SCENES } from "../../app/scenes";
import { WorkspaceFrame } from "../../design-system/WorkspaceFrame";
import AgentComposer from "../../app/AgentComposer";
import { requestIdForRun, StatusBadge, Icon } from "../../app/ui";
import { t } from "../../app/i18n";
import type { RunCtx } from "../../app/runCtx";
import { WorkspaceTasks } from "../../app/WorkspaceTasks";
import type { WorkspaceFileSummary } from "../../types/service";
import { ForgeDocumentSession } from "./documentSession";

/** Forge per hand-drawn spec §6 — mode rail | file tree | tabs+editor w/
 *  line numbers | right control rail | AgentComposer. Code mode first. */
const MODES = [
  { id: "code", icon: "file-code", label: "Code", ready: true },
  { id: "data", icon: "list", label: "Data", ready: false },
  { id: "eng", icon: "tools", label: "Eng", ready: false },
  { id: "cad", icon: "layers", label: "CAD", ready: false },
];

// Drafts survive leaving the workspace — sessions are cached per
// tenant:session so navigating away and back restores the open document,
// its baseline and its dirty draft. Entries are bound to a service
// identity; a service swap (e.g. backend restart) gets a fresh session.
const sessionCache = new Map<string, ForgeDocumentSession>();

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
  const session = useMemo(() => {
    const key = `${service.identity.tenantId}:${service.identity.sessionId}`;
    let cached = sessionCache.get(key);
    if (!cached || cached.serviceRef !== service) {
      cached = new ForgeDocumentSession(
        service,
        () => ({ tenantId: service.identity.tenantId, sessionId: service.identity.sessionId }),
        () => bump((v) => v + 1),
      );
      sessionCache.set(key, cached);
    }
    return cached;
  }, [service]);
  const doc = session.doc;

  const sessionId = ctx.sessionId;
  useEffect(() => {
    const identity = { tenantId: service.identity.tenantId, sessionId };
    service
      .listWorkspaceFiles({
        ...identity,
        requestId: requestIdForRun(0),
      })
      .then((list) => {
        // a slow A response must not land on session B's list
        if (identity.sessionId === ctx.sessionId) setFiles(list);
      })
      .catch((e) => setErr(String(e)));
  }, [service, sessionId, ctx.sessionId]);

  async function openFile(path: string) {
    setErr(null);
    try {
      if (!(await session.open(path))) {
        if (!window.confirm(t("mar.discard", { path: session.doc?.path ?? "current file" }))) return;
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
            <button className={`forge-mode ${mode === m.id ? "on" : ""}`} key={m.id} disabled={!m.ready}
              onClick={() => setMode(m.id)} title={m.ready ? m.label : `${m.label} — ${t("mar.mode-na")}`} type="button">
              <Icon name={m.icon as never} size={16} /><span>{m.label}</span>
            </button>
          ))}
        </nav>
        {/* ProjectFileTree */}
        <aside className="forge-tree">
          <h4 className="ws-mod">{t("mar.explorer")}</h4>
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
              <small>{ctx.activeRun ? <code>{ctx.activeRun.runId} · {ctx.activeRun.status}</code> : t("mar.no-active")}</small></div>
          </div>
          <div className="forge-tabs">
            {doc ? (
              <span className="forge-tab on">
                {doc.path}{session.dirty ? " ●" : ""}{doc.redacted ? ` · ${t("mar.readonly")}` : ""}
              </span>
            ) : <span className="forge-tab">{t("mar.no-file")}</span>}
            {doc && !doc.redacted && (
              <button className="forge-save" disabled={!session.dirty || session.saving} onClick={() => void save()} type="button">
                {session.saving ? "…" : t("mar.save")}
              </button>
            )}
          </div>
          <div className="forge-code">
            {doc ? (
              <textarea className="forge-editor" value={session.content} readOnly={doc.redacted}
                onChange={(e) => session.edit(e.target.value)} spellCheck={false} />
            ) : <div className="empty-state"><p>{t("mar.select-file")}</p></div>}
          </div>
        </main>
        {/* EngineeringActionPanel */}
        <aside className="forge-ctl">
          {activeRun && <div className="forge-status"><StatusBadge status={activeRun.status} /><code>{activeRun.runId}</code></div>}
          <div className="forge-ctl-hint">{t("mar.controls")}</div>
          <AgentComposer ctx={ctx} planet="mars" placeholder={t("mar.composer-ph")} />
                    <WorkspaceTasks ctx={ctx} planet="mars" />
          <div className="ws-statusbar"><span>{files.length} FILES</span><span>{mode.toUpperCase()}</span><span className="right">mars</span></div>
        </aside>
      </div>
    </WorkspaceFrame>
  );
}
