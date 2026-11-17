import { useState } from "react";
import { t } from "./i18n";
import type { RunCtx } from "./runCtx";
import { pickFolder } from "../service/tauriBridge";

/** What WorkspaceHost resolved for the live scope — the actual effective
 *  root, marked whether a user-chosen workspace backs it. */
export interface ResolvedDir {
  path: string;
  /** True only for a user-bound workspace (not the app-managed auto
   *  root and not a resolver default) — drives the unbind action. */
  userBound: boolean;
}

/** Shared per-workstation directory bar — shows the session's resolved
 *  effective workspace root and binds/rebinds through the same
 *  session→workspace contract. App-managed roots display as themselves
 *  with an auto badge; there is no Home fallback. */
export function WorkspaceDirBar({ ctx, resolved, onBound }: { ctx: RunCtx; resolved: ResolvedDir | null; onBound: () => void }) {
  const service = ctx.service;
  const [busy, setBusy] = useState(false);

  const bind = (picked: { name: string; path: string }) => {
    setBusy(true);
    void service.upsertWorkspace({
      sessionId: ctx.sessionId,
      workspaceId: picked.path,
      displayName: picked.name,
      path: picked.path,
    }).then(() => service.assignSessionWorkspace({
      sessionId: ctx.sessionId,
      workspaceId: picked.path,
    })).then(onBound).finally(() => setBusy(false));
  };

  const unbind = () => {
    setBusy(true);
    void service.assignSessionWorkspace({
      sessionId: ctx.sessionId,
      workspaceId: "",
    }).then(onBound).finally(() => setBusy(false));
  };

  return (
    <div className="ws-dirbar" title={resolved?.path || undefined}>
      <span className="ws-dir-icon">📁</span>
      <code className="ws-dir-path">{resolved?.path || t("wsdir.none")}</code>
      {resolved && !resolved.userBound && <span className="ws-dir-default">{t("wsdir.default")}</span>}
      <button
        className="btn secondary ws-dir-btn"
        disabled={busy}
        onClick={() => void pickFolder().then((p) => { if (p) bind(p); })}
        type="button"
      >{t("wsdir.pick")}</button>
      {resolved?.userBound && (
        <button className="btn secondary ws-dir-btn" disabled={busy} onClick={unbind} type="button">
          {t("wsdir.unbind")}
        </button>
      )}
    </div>
  );
}
