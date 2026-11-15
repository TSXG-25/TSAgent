import { Suspense, lazy, useCallback, useEffect, useState } from "react";
import { Navigate, useParams } from "react-router-dom";
import { resolveScene, type SceneId } from "./scenes";
import { resolveBoundPath } from "./sessionWorkspace";
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
  // `planet` claim); revisits reload that scope's catalog. Switching
  // workstations never touches another scope's session.
  useEffect(() => {
    if (planet) void ctx.ensurePlanet(planet);
  }, [ctx, planet]);
  // Resolve this scope's durable session→workspace binding — files,
  // attachments, submissions and the Mars IDE all read the same root.
  // Unbound scopes still have an effective root: the backend's factory
  // default, surfaced via listDirectory (never a guessed path).
  const [boundPath, setBoundPath] = useState("");
  const [effectivePath, setEffectivePath] = useState("");
  const refreshWsPath = useCallback(() => {
    if (!sessionId) return;
    void resolveBoundPath(ctx.service, sessionId)
      .then(async (bound) => {
        setBoundPath(bound);
        setEffectivePath(bound || (await ctx.service.listDirectory("")).path);
      })
      .catch(() => { setBoundPath(""); setEffectivePath(""); });
  }, [ctx.service, sessionId]);
  useEffect(refreshWsPath, [refreshWsPath]);
  // Dev browser folder picking needs the local sidecar's native dialog —
  // register it for every scene so planet directory bars work too.
  useEffect(() => {
    if (ctx.service.mode !== "local") return;
    setSidecarPicker(() => ctx.service.pickFolder());
    return () => setSidecarPicker(null);
  }, [ctx.service]);
  if (!scene) return <Navigate to="/" replace />;
  if (!sessionId) return <div className="ws-loading">Preparing {scene.workspace}…</div>;
  const wctx: RunCtx = {
    ...ctx,
    sessionId,
    workspacePath: effectivePath || undefined,
    selectSession: (id: string) => ctx.selectSessionFor(planet, id),
  };
  const Workspace = LAZY[scene.id];
  return (
    <>
      {scene.id !== "sun" && <WorkspaceDirBar ctx={wctx} path={boundPath} onBound={refreshWsPath} />}
      <Suspense fallback={<div className="ws-loading">Loading {scene.workspace}…</div>}>
        <Workspace ctx={wctx} />
      </Suspense>
    </>
  );
}
