import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Routes, Route, useNavigate, useLocation, Link } from "react-router-dom";
import type { ServiceErrorDTO } from "./types/service";
import { createAgentServiceClient } from "./service/clientFactory";
import { RunController, type RunControllerState } from "./features/runs/runController";
import MissionControl from "./components/MissionControl";
import WorkspaceHost from "./app/WorkspaceHost";
import GlobalSettings from "./app/GlobalSettings";
import TaskTray from "./app/TaskTray";
import GlobalChat from "./app/GlobalChat";
import PlanetSwitcher from "./app/PlanetSwitcher";
import { SCENES, PLANET_GLYPH, type SceneDef } from "./app/scenes";
import { motionTier } from "./app/motionTier";
import { draftKey, useDraft, clearDraftIfUnchanged } from "./app/draftStore";
import { loadPlanetSessions, savePlanetSessions } from "./app/workstationScopes";
import { t, tk, useLang } from "./app/i18n";
/** §3 route-jump — planet glyph sweep on every workspace entry (starmap,
 *  switcher or keyboard). 620ms, skipped on first mount and reduced-motion. */
function SceneJump({ path }: { path: string }) {
  const [flash, setFlash] = useState<SceneDef | null>(null);
  const prev = useRef<string>("");
  useEffect(() => {
    const id = path.startsWith("/w/") ? path.slice(3) : null;
    const sc = id ? SCENES.find((x) => x.id === id) ?? null : null;
    const isFirst = prev.current === "";
    const changed = prev.current !== path;
    prev.current = path;
    if (!sc || isFirst || !changed) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    setFlash(sc);
    const t = setTimeout(() => setFlash(null), 920);
    return () => clearTimeout(t);
  }, [path]);
  if (!flash) return null;
  return (
    <div className="scene-jump" style={{ "--planet-accent": flash.accent } as React.CSSProperties} aria-live="polite">
      <div className="sj-sweep" />
      <div className="sj-core">
        <span className="sj-glyph">{PLANET_GLYPH[flash.id]}</span>
        <strong>{flash.workspace}</strong>
        <span className="sj-sub">{flash.name}</span>
      </div>
    </div>
  );
}

import Cursor from "./components/Cursor";
import { resolveScene } from "./app/scenes";
import type { RunCtx } from "./app/runCtx";
import { Icon, toServiceError, requestIdForRun } from "./app/ui";
import "./styles.css";
import "./design-system/tokens.css";

function App() {
  const service = useMemo(() => createAgentServiceClient(), []);
  const controller = useMemo(() => new RunController(service), [service]);
  const runtimeLabel = service.mode === "local" ? "Local AgentService" : "Mock AgentService";
  const navigate = useNavigate();
  const location = useLocation();
  const sceneId = location.pathname.startsWith("/w/") ? location.pathname.slice(3) : null;
  const scene = resolveScene(sceneId ?? undefined);

  const [controllerState, setControllerState] = useState<RunControllerState>(() => controller.getState());
  const runs = controllerState.runs;
  const [latency, setLatency] = useState<number | null>(null);
  useEffect(() => {
    let dead = false;
    const ping = async () => {
      const t0 = performance.now();
      try {
        await service.getSettings({ tenantId: service.identity.tenantId, sessionId: service.identity.sessionId, requestId: `telemetry-${Date.now()}` });
        if (!dead) setLatency(Math.round(performance.now() - t0));
      } catch { if (!dead) setLatency(null); }
    };
    void ping();
    const t = setInterval(() => void ping(), 30_000);
    return () => { dead = true; clearInterval(t); };
  }, [service]);
  const runningCount = runs.filter((r) => r.status === "active").length;
  // T2 — workspace → starmap exit collapse
  const [exitVeil, setExitVeil] = useState(false);
  const prevPath = useRef(location.pathname);
  useEffect(() => {
    const leaving = prevPath.current.startsWith("/w/") && location.pathname === "/";
    prevPath.current = location.pathname;
    if (!leaving || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    setExitVeil(true);
    const t = setTimeout(() => setExitVeil(false), 320);
    return () => clearTimeout(t);
  }, [location.pathname]);
  const activeRunId = controllerState.activeRunId;
  // Per-workstation scope map — each planet owns an independently persisted
  // current session (durable `planet` claim on the session head). The base
  // ctx session is Sun/Omni's own scope.
  const [planetSessions, setPlanetSessions] = useState<Record<string, string>>(() => ({
    sun: service.identity.sessionId,
    ...loadPlanetSessions(service.identity.tenantId, service.identity.userId),
  }));
  const sessionId = planetSessions.sun ?? service.identity.sessionId;
  const planetSessionsRef = useRef(planetSessions);
  planetSessionsRef.current = planetSessions;
  const ensureInflight = useRef(new Map<string, Promise<string>>());
  useEffect(() => {
    savePlanetSessions(service.identity.tenantId, service.identity.userId, planetSessions);
  }, [planetSessions, service]);
  // One subscription at the root — language changes re-render the whole
  // tree so every t() call site sees the new locale.
  useLang();
  // The new-task draft is the "sun" target draft — shared with the Sun
  // workspace composer and GlobalChat's direct path per the draft contract.
  const requestDraftKey = draftKey(service.identity.tenantId, sessionId, "sun");
  const [requestDraftState, setRequestDraftState] = useDraft(requestDraftKey);
  const requestDraft = requestDraftState.text;
  const setRequestDraft = (value: string) => setRequestDraftState({ ...requestDraftState, text: value });
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [isChatOpen, setIsChatOpen] = useState(false);
  const [chatInstant, setChatInstant] = useState(false);
  const [serviceError, setServiceError] = useState<ServiceErrorDTO | null>(null);
  const isResuming = controllerState.operationState === "resuming";
  const isCancelling = controllerState.operationState === "cancelling";

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "n") {
        event.preventDefault();
        setIsCreateOpen(true);
      } else if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "l") {
        event.preventDefault();
        setChatInstant(true);
        setIsChatOpen((v) => !v);
      } else if (event.key === "Escape") {
        setIsCreateOpen(false);
        setIsChatOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function selectSessionFor(planet: string, id: string) {
    void service.listSessions({
      tenantId: service.identity.tenantId,
      userId: service.identity.userId,
      requestId: `sess-lookup-${Date.now()}`,
    }).then((list) => {
      const target = list.find((s) => s.sessionId === id);
      if (target?.planet && target.planet !== planet) return; // foreign-owned — view only, no claim
      return service.createSession({
        tenantId: service.identity.tenantId,
        userId: service.identity.userId,
        sessionId: id,
        requestId: `claim-${planet}-${Date.now()}`,
        planet,
      }).then(() => undefined);
    }).then(() => {
      setPlanetSessions((m) => ({ ...m, [planet]: id }));
      void controller.loadScope(id);
    }).catch((error) => setServiceError(toServiceError(error)));
  }

  function ensurePlanet(planet: string): Promise<string> {
    const existing = planetSessionsRef.current[planet];
    if (existing) {
      void controller.loadScope(existing);
      return Promise.resolve(existing);
    }
    let pending = ensureInflight.current.get(planet);
    if (!pending) {
      pending = (async () => {
        const sid = `session-${planet}-${crypto.randomUUID().slice(0, 8)}`;
        await service.createSession({
          tenantId: service.identity.tenantId,
          userId: service.identity.userId,
          sessionId: sid,
          requestId: `scope-${planet}-${Date.now()}`,
          planet,
        });
        setPlanetSessions((m) => (m[planet] ? m : { ...m, [planet]: sid }));
        void controller.loadScope(sid);
        return sid;
      })();
      ensureInflight.current.set(planet, pending);
    }
    return pending;
  }

  useEffect(() => {
    const unsubscribe = controller.subscribe(setControllerState);
    // Claim Sun's bootstrap session, then hydrate every known scope — each
    // loadScope only touches its own session's catalog entries.
    void service.createSession({
      tenantId: service.identity.tenantId,
      userId: service.identity.userId,
      sessionId,
      requestId: `scope-sun-${Date.now()}`,
      planet: "sun",
    }).catch((error) => setServiceError(toServiceError(error)));
    void controller.loadScope(sessionId);
    for (const [planet, sid] of Object.entries(planetSessionsRef.current)) {
      if (planet !== "sun") void controller.loadScope(sid);
    }
    return () => {
      unsubscribe();
      controller.dispose();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- boot once per client
  }, [controller]);

  const activeRun = runs.find((run) => run.runId === activeRunId) ?? runs[0];

  const visibleServiceError = serviceError ?? controllerState.error;

  if (controllerState.isLoading && !activeRun) {
    return (
      <div className="app-loading">
        <div>{visibleServiceError ? `${visibleServiceError.code}: ${visibleServiceError.message}` : `Connecting to ${runtimeLabel}…`}</div>
      </div>
    );
  }

  const workflow = activeRun?.workflows.find((item) => item.workflowId === activeRun.activeWorkflowId) ?? activeRun?.workflows[0] ?? null;
  const canResume = activeRun?.status === "blocked" && activeRun.resume?.outcome === "ready";
  const canCancel = activeRun?.status === "active";

  function selectRun(runId: string) {
    if (!runs.find((run) => run.runId === runId)) return;
    controller.selectRun(runId);
  }

  function openRun(runId: string, runSessionId?: string) {
    // The store is a union across scopes — refresh writes the target into
    // its owning session's slice (beyond the catalog page is fine), then
    // select + navigate. No session switching needed anymore.
    void controller.refreshRun(runId, runSessionId)
      .then((view) => {
        if (view) controller.selectRun(runId);
        navigate("/w/sun");
      })
      .catch((error) => setServiceError(toServiceError(error)));
  }

  async function refreshActiveRun() {
    try {
      if (activeRun) await controller.refreshRun(activeRun.runId);
    } catch (error) {
      setServiceError(toServiceError(error));
    }
  }

  async function cancelActiveRun() {
    if (!canCancel || isCancelling) return;
    try {
      setServiceError(null);
      if (activeRun) await controller.cancelRun(activeRun.runId);
    } catch (error) {
      setServiceError(toServiceError(error));
    }
  }

  async function createRun(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    const submitted = requestDraftState;
    const request = requestDraft.trim() || "生成一个 Python 程序，计算 9 的平方并保存";
    const requestId = requestIdForRun(runs.length + 1);
    // Submission target follows where the user is: inside a workstation the
    // global new-task modal creates that workstation's task (its own
    // session + durable originPlanet); from the map/Omni it targets Sun.
    const target = scene?.id ?? "sun";
    try {
      setServiceError(null);
      const targetSession = target === "sun" ? sessionId : await ensurePlanet(target);
      await controller.startRun({
        tenantId: service.identity.tenantId,
        sessionId: targetSession,
        requestId,
        requestText: request,
        contextRefs: submitted.files,
        originPlanet: target,
      });
      clearDraftIfUnchanged(requestDraftKey, submitted);
      setIsCreateOpen(false);
      navigate("/w/sun");
    } catch (error) {
      setServiceError(toServiceError(error));
    }
  }

  async function resumeActiveRun() {
    if (!canResume) return;
    try {
      setServiceError(null);
      if (activeRun) await controller.resumeRun(activeRun.runId);
    } catch (error) {
      setServiceError(toServiceError(error));
    }
  }

  function selectSession(id: string) {
    selectSessionFor("sun", id);
  }

  const ctx: RunCtx = {
    service,
    sessionId,
    selectSession,
    ensurePlanet,
    sessionForPlanet: (planet: string) => planetSessions[planet] ?? "",
    selectSessionFor,
    runtimeLabel,
    controller,
    runs,
    activeRun,
    workflow,
    requestDraft,
    setRequestDraft,
    createRun,
    selectRun,
    openRun,
    openNewTask: () => setIsCreateOpen(true),
    refreshActiveRun: () => void refreshActiveRun(),
    cancelActiveRun: () => void cancelActiveRun(),
    resumeActiveRun: () => void resumeActiveRun(),
    canCancel,
    canResume,
    isCancelling,
    isResuming,
  };
  // The floating dock opened from a workstation uses that workstation's
  // scope when it already has one — not always Sun's session.
  const chatCtx: RunCtx = scene && scene.id !== "sun" && planetSessions[scene.id]
    ? { ...ctx, sessionId: planetSessions[scene.id], selectSession: (id: string) => selectSessionFor(scene.id, id) }
    : ctx;

  return (
    <div className="app-shell" data-planet={scene?.id ?? "selector"}>
      <Cursor />
      <header className="topbar app-topbar">
        <Link className="topbar-brand" to="/" type="button">
          <div className="product-mark"><Icon name="spark" size={16} /></div>
          <strong>T.S.A</strong>
        </Link>
        {service.mode === "mock" && <span className="demo-badge">DEMO</span>}
        <PlanetSwitcher scene={scene ?? null} />
        {scene && scene.id !== "sun" && scene.id !== "mars" && (
          <button className="dock-pill" onClick={() => { setChatInstant(false); setIsChatOpen(true); }} title="Agent dock (⌘L)" type="button">
            @{scene.id} <kbd>⌘L</kbd>
          </button>
        )}
        {activeRun && (
          <button className="run-capsule" title="Open task tray" type="button">
            <span className={`st-dot ${activeRun.status}`} />
            <code>{activeRun.runId}</code>
            <strong>{activeRun.status.toUpperCase()}</strong>
          </button>
        )}
        <span className="topbar-spacer" />
        <TaskTray ctx={ctx} />
        <button className="topbar-button" onClick={() => setIsCreateOpen(true)} title="New task (⌘N)" type="button"><Icon name="plus" size={15} /></button>
        <button className="topbar-button" onClick={() => setIsSettingsOpen(true)} title="Global settings" type="button"><Icon name="settings" size={15} /></button>
        {scene && <Link className="topbar-button" to="/" title="星图视图" type="button"><Icon name="layers" size={15} /></Link>}
      </header>

      {visibleServiceError && <div className="service-error-banner" role="alert"><span className="service-error-icon"><Icon name="x" size={13} /></span><div><strong>{tk(`err.${visibleServiceError.code}`) ?? visibleServiceError.code}</strong><span>{visibleServiceError.code}: {visibleServiceError.message}</span></div><button onClick={() => { setServiceError(null); controller.dismissError(); }} title={t("err.dismiss")} type="button"><Icon name="x" size={13} /></button></div>}

      <main className="route-host">
        <Routes>
          <Route index element={
            <MissionControl
              activeRun={activeRun}
              workflow={workflow}
              runs={runs}
              runtimeLabel={runtimeLabel}
              requestDraft={requestDraft}
              setRequestDraft={setRequestDraft}
              createRun={createRun}
              openRun={openRun}
              canCancel={canCancel}
              canResume={canResume}
              isCancelling={isCancelling}
              isResuming={isResuming}
              onCancel={() => void cancelActiveRun()}
              onResume={() => void resumeActiveRun()}
              onRefresh={() => void refreshActiveRun()}
              Icon={Icon}
            />
          } />
          <Route path="w/:sceneId" element={<WorkspaceHost ctx={ctx} />} />
        </Routes>
      </main>

      {isSettingsOpen && <GlobalSettings service={service} sessionId={sessionId} runs={ctx.runs} onClose={() => setIsSettingsOpen(false)} />}
      {isChatOpen && <GlobalChat ctx={chatCtx} instant={chatInstant} onClose={() => setIsChatOpen(false)} />}
      <SceneJump path={location.pathname} />
      {exitVeil && <div className="ws-exit" />}
      <TelemetryBar latency={latency} running={runningCount} />

      {isCreateOpen && (
        <div className="modal-backdrop" onMouseDown={() => setIsCreateOpen(false)}>
          <div className="create-modal glass" onMouseDown={(event) => event.stopPropagation()}>
            <header className="modal-heading">
              <div><h2>New Task</h2></div>
              <button className="modal-close" onClick={() => setIsCreateOpen(false)} type="button"><Icon name="x" size={16} /></button>
            </header>
            <form className="create-form" onSubmit={createRun}>
              <div className="field"><label>请求</label>
                <textarea autoFocus onChange={(e) => setRequestDraft(e.target.value)} placeholder="例如：生成一个 Python 程序，计算 7 的平方并保存" rows={5} value={requestDraft} /></div>
              <div className="modal-footer">
                <span className="omni-meta">{runtimeLabel} · AgentService Contract</span>
                <button className="btn primary" type="submit">Create Run</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;

/** §4.4 topbar telemetry — real data only: RPC latency + running tasks. */
function TelemetryBar({ latency, running }: { latency: number | null; running: number }) {
  if (motionTier() === "minimal") return null;
  return (
    <div className="telemetry" aria-live="off">
      {latency !== null && <span className="tm-item"><em>{latency}ms</em><i>延迟</i></span>}
      <span className="tm-item"><em>{running}</em><i>运行中</i></span>
    </div>
  );
}
