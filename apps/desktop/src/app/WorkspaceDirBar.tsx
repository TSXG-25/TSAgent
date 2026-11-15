import { useEffect, useState } from "react";
import { t } from "./i18n";
import type { RunCtx } from "./runCtx";
import { pickFolder } from "../service/tauriBridge";

/** Shared per-workstation directory bar — shows the session's durable
 *  workspace binding (or the backend default root when unbound), and
 *  binds/rebinds through the same session→workspace contract Sun uses. */
export function WorkspaceDirBar({ ctx, path, onBound }: { ctx: RunCtx; path: string; onBound: () => void }) {
  const service = ctx.service;
  const [defaultRoot, setDefaultRoot] = useState("");
  const [busy, setBusy] = useState(false);
  // Unbound sessions still show their actual effective root — the backend
  // factory root, resolved via listDirectory (never a guessed path).
  useEffect(() => {
    if (path) { setDefaultRoot(""); return; }
    void service.listDirectory("").then((l) => setDefaultRoot(l.path)).catch(() => setDefaultRoot(""));
  }, [service, path]);

  const bind = (picked: { name: string; path: string }) => {
    setBusy(true);
    const tenantId = service.identity.tenantId;
    void service.upsertWorkspace({
      tenantId,
      sessionId: ctx.sessionId,
      requestId: `dir-ws-${Date.now()}`,
      workspaceId: picked.path,
      displayName: picked.name,
      path: picked.path,
    }).then(() => service.assignSessionWorkspace({
      tenantId,
      sessionId: ctx.sessionId,
      requestId: `dir-as-${Date.now()}`,
      workspaceId: picked.path,
    })).then(onBound).finally(() => setBusy(false));
  };

  const unbind = () => {
    setBusy(true);
    void service.assignSessionWorkspace({
      tenantId: service.identity.tenantId,
      sessionId: ctx.sessionId,
      requestId: `dir-un-${Date.now()}`,
      workspaceId: "",
    }).then(onBound).finally(() => setBusy(false));
  };

  const shown = path || defaultRoot;
  return (
    <div className="ws-dirbar" title={shown || undefined}>
      <span className="ws-dir-icon">📁</span>
      <code className="ws-dir-path">{shown || t("wsdir.none")}</code>
      {!path && shown && <span className="ws-dir-default">{t("wsdir.default")}</span>}
      <button
        className="btn secondary ws-dir-btn"
        disabled={busy}
        onClick={() => void pickFolder().then((p) => { if (p) bind(p); })}
        type="button"
      >{t("wsdir.pick")}</button>
      {path && (
        <button className="btn secondary ws-dir-btn" disabled={busy} onClick={unbind} type="button">
          {t("wsdir.unbind")}
        </button>
      )}
    </div>
  );
}
