import { Suspense, lazy, useEffect } from "react";
import { Navigate, useParams } from "react-router-dom";
import { resolveScene, type SceneId } from "./scenes";
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
  if (!scene) return <Navigate to="/" replace />;
  if (!sessionId) return <div className="ws-loading">Preparing {scene.workspace}…</div>;
  const wctx: RunCtx = {
    ...ctx,
    sessionId,
    selectSession: (id: string) => ctx.selectSessionFor(planet, id),
  };
  const Workspace = LAZY[scene.id];
  return (
    <Suspense fallback={<div className="ws-loading">Loading {scene.workspace}…</div>}>
      <Workspace ctx={wctx} />
    </Suspense>
  );
}
