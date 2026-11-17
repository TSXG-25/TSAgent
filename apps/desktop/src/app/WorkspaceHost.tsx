import { Suspense, lazy, useCallback, useEffect, useRef, useState } from "react";
import { Navigate, useParams } from "react-router-dom";
import { resolveScene, type SceneId } from "./scenes";
import { WorkspaceDirBar } from "./WorkspaceDirBar";
import { setSidecarPicker } from "../service/tauriBridge";
import type { RunCtx } from "./runCtx";

const LAZY: Record<SceneId, React.LazyExoticComponent<(props: { ctx: RunCtx }) => React.JSX.Element>> = {
  sun: lazy(() => import("../workspaces/sun/SunWorkspace")),
  jupiter: lazy(() => import("../workspaces/jupiter/JupiterWorkspace")),
  mercury: lazy(() => import("../workspaces/mercury/MercuryWorkspace")),
  venus: lazy(() => import("../workspaces/venus/VenusWorkspace")),
  earth: lazy(() => import("../workspaces/earth/EarthWorkspace")),
  mars: lazy(() => import("../workspaces/mars/MarsWorkspace")),
  saturn: lazy(() => import("../workspaces/saturn/SaturnWorkspace")),
  uranus: lazy(() => import("../workspaces/uranus/UranusWorkspace")),
  neptune: lazy(() => import("../workspaces/neptune/NeptuneWorkspace")),
  pluto: lazy(() => import("../workspaces/pluto/PlutoWorkspace")),
};

export default function WorkspaceHost({ ctx }: { ctx: RunCtx }) {
  const { sceneId } = useParams();
  const scene = resolveScene(sceneId);
  const planet = scene?.id ?? "";
  const sessionId = ctx.sessionForPlanet(planet);
  // First visit to a workstation creates its own session scope (durable
  // `planet` claim). Depends on the stable planet/session primitives —
  // a new ctx identity per App render must not re-fire scope creation.
  useEffect(() => {
    if (planet && !sessionId) void ctx.ensurePlanet(planet);
  }, [planet, sessionId]);
  // Resolve this scope's effective workspace root via the authoritative
  // backend contract (durable binding, or the resolver/app-managed root
  // it registers + binds). The result stays bound to the session it was
  // requested for: a switch clears it immediately, a late response for
  // the old session is discarded, and submissions can never see a stale
  // root — workspacePath is undefined until the live scope resolves.
  const [ws, setWs] = useState<{ sessionId: string; path: string; bound: boolean; workspaceId: string } | null>(null);
  const sessionRef = useRef(sessionId);
  sessionRef.current = sessionId;
  const refreshWsPath = useCallback(() => {
    if (!sessionId) return;
    const target = sessionId;
    void ctx.service.resolveSessionWorkspace({
      tenantId: ctx.service.identity.tenantId,
      sessionId: target,
    }).then((r) => {
      // Live-scope check via ref — the closure's sessionId is stale by
      // the time a slow response lands.
      if (sessionRef.current === target) {
        setWs({ sessionId: r.sessionId, path: r.path, bound: r.bound, workspaceId: r.workspaceId });
      }
    }).catch(() => {
      if (sessionRef.current === target) setWs(null);
    });
  }, [ctx.service, sessionId]);
  useEffect(() => {
    setWs((cur) => (cur && cur.sessionId === sessionId ? cur : null));
    refreshWsPath();
  }, [refreshWsPath, sessionId]);
  // Dev browser folder picking needs the local sidecar's native dialog —
  // register it for every scene so planet directory bars work too.
  useEffect(() => {
    if (ctx.service.mode !== "local") return;
    setSidecarPicker(() => ctx.service.pickFolder());
    return () => setSidecarPicker(null);
  }, [ctx.service]);
  if (!scene) return <Navigate to="/" replace />;
  if (!sessionId) return <div className="ws-loading">Preparing {scene.workspace}…</div>;
  // Only a resolution owned by the live scope may reach consumers —
  // during a switch workspacePath is undefined, never the old session's.
  const active = ws && ws.sessionId === sessionId ? ws : null;
  const wctx: RunCtx = {
    ...ctx,
    sessionId,
    workspacePath: active?.path || undefined,
    workspaceUserBound: active ? active.bound && !active.workspaceId.startsWith("ws-auto-") : false,
    onWorkspaceBound: refreshWsPath,
    selectSession: (id: string) => ctx.selectSessionFor(planet, id),
  };
  const Workspace = LAZY[scene.id];
  return (
    // .ws-host is the column that owns the dirbar-to-workspace order —
    // the route host is a row flex, so a bare fragment would drop the
    // bar as a vertical strip on the left edge instead of a top bar.
    <div className="ws-host">
      {/* Mars embeds the dir controls inside its IDE pane; other
       *  workstations get the shared bar above the workspace. */}
      {scene.id !== "sun" && scene.id !== "mars" && (
        <WorkspaceDirBar ctx={wctx} onBound={refreshWsPath}
          resolved={active ? {
            path: active.path,
            // 应用管理根（ws-auto-*）与未绑定 resolver 根都算"自动"，
            // 如实展示实际路径，只把用户选择标为可解绑。
            userBound: active.bound && !active.workspaceId.startsWith("ws-auto-"),
          } : null} />
      )}
      <Suspense fallback={<div className="ws-loading">Loading {scene.workspace}…</div>}>
        <Workspace ctx={wctx} />
      </Suspense>
    </div>
  );
}
