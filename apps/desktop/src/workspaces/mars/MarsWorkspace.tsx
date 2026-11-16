import { useEffect, useMemo, useRef, useState } from "react";
import { SCENES } from "../../app/scenes";
import { WorkspaceFrame } from "../../design-system/WorkspaceFrame";
import { t } from "../../app/i18n";
import type { RunCtx } from "../../app/runCtx";
import { WorkspaceRail } from "../../app/WorkspaceRail";
import { WorkspaceDirBar } from "../../app/WorkspaceDirBar";
import { ideBridge } from "../../service/tauriBridge";
import { MarsIdeSession } from "./ideSession";
import { ideFilePrefix } from "../../app/ideContext";

/** Mars = hosted code-server IDE + agent rail. The IDE is always-on: it
 *  auto-starts on the session's resolved workspace and follows every
 *  rebind/unbind, so there is no start/stop control. Agent tasks run
 *  against the same session workspace root — files an agent writes land
 *  in the directory code-server shows. The dir bar lives inside the IDE
 *  pane header (the only place binding is needed). The rail keeps the
 *  workstation's own records: "+" opens its home (recent sessions) and a
 *  send there files a fresh session; @-mentions are the only bridge out. */
export default function MarsWorkspace({ ctx }: { ctx: RunCtx }) {
  const scene = SCENES[4];
  const [, bump] = useState(0);

  /** Hosted IDE — MarsIdeSession binds the process to the live scope:
   *  a bound workspace auto-starts it, a session/workspace change or
   *  unmount releases it, and a start resolving after the scope moved on
   *  is dropped and killed — no instance binds a stale root. */
  const ide = useMemo(() => (ideBridge ? new MarsIdeSession(ideBridge) : null), []);
  const sessionId = ctx.sessionId;
  const workspacePath = ctx.workspacePath ?? "";
  const ideScope = `${sessionId}|${workspacePath}`;
  useEffect(() => {
    if (!ide) return;
    ide.listener = () => bump((v) => v + 1);
    void ide.bindScope(ideScope, workspacePath);
    return () => {
      ide.listener = null;
      void ide.release();
    };
  }, [ide, ideScope, workspacePath]);

  const resolved = workspacePath
    ? { path: workspacePath, userBound: ctx.workspaceUserBound ?? false }
    : null;

  /** Send-time context: the file focused in the hosted IDE travels with
   *  the request so "解释下代码" knows what "这" points at. Paths outside
   *  the bound workspace (a stale marker from a previous scope) never
   *  leak into the request. */
  const sendContext = useMemo(() => {
    const bridge = ideBridge;
    if (!bridge || !workspacePath) return undefined;
    return async () => {
      const path = await bridge.activeFile();
      return path.startsWith(`${workspacePath}/`) ? ideFilePrefix(path) : "";
    };
  }, [workspacePath]);

  /** Rail width — user-adjustable by dragging the left edge, persisted
   *  per workstation. flexBasis + min-width:0 keep it exact: unbreakable
   *  content (paths, code) can never push the panel wider mid-stream. */
  const [railW, setRailW] = useState(() => Number(localStorage.getItem("tsagent.railW.mars")) || 340);
  useEffect(() => localStorage.setItem("tsagent.railW.mars", String(railW)), [railW]);
  const drag = useRef<{ x: number; w: number } | null>(null);

  return (
    <WorkspaceFrame scene={scene} ctx={ctx}>
      <div className="forge mars-ide">
        <main className="forge-editor">
          <WorkspaceDirBar ctx={ctx} resolved={resolved} onBound={ctx.onWorkspaceBound ?? (() => {})} />
          <div className="forge-code forge-ide-host">
            {!ide ? (
              <div className="empty-state"><p>{t("mar.ide-na")}</p></div>
            ) : !workspacePath ? (
              <div className="empty-state"><p>{t("mar.ide-nows")}</p></div>
            ) : ide.info ? (
              <iframe
                className="forge-ide"
                src={`${ide.info.url}?folder=${encodeURIComponent(ide.info.workspace)}`}
                title="code-server"
              />
            ) : (
              <div className="empty-state">
                <p>{ide.busy ? t("mar.ide-starting") : (ide.error ?? t("mar.ide-failed"))}</p>
              </div>
            )}
          </div>
        </main>
        <aside className="forge-ctl" style={{ flexBasis: railW }}>
          <div
            className="rail-resize"
            role="separator"
            aria-orientation="vertical"
            onPointerDown={(e) => { drag.current = { x: e.clientX, w: railW }; e.currentTarget.setPointerCapture(e.pointerId); }}
            onPointerMove={(e) => { const d = drag.current; if (d) setRailW(Math.min(720, Math.max(260, d.w + (d.x - e.clientX)))); }}
            onPointerUp={() => { drag.current = null; }}
            onPointerCancel={() => { drag.current = null; }}
          />
          <WorkspaceRail ctx={ctx} planet="mars" placeholder={t("mar.composer-ph")} sendContext={sendContext} />
          <div className="ws-statusbar"><span className="right">mars</span></div>
        </aside>
      </div>
    </WorkspaceFrame>
  );
}
