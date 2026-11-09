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
  const [sessionId, setSessionId] = useState(service.identity.sessionId);
  const [requestDraft, setRequestDraft] = useState("");
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

  useEffect(() => {
    const unsubscribe = controller.subscribe(setControllerState);
    void controller.initialize();
    return () => {
      unsubscribe();
      controller.dispose();
    };
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

  function openRun(runId: string) {
    selectRun(runId);
    navigate("/w/sun");
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
    const request = requestDraft.trim() || "生成一个 Python 程序，计算 9 的平方并保存";
    const requestId = requestIdForRun(runs.length + 1);
    try {
      setServiceError(null);
      await controller.startRun({
        tenantId: service.identity.tenantId,
        sessionId,
        requestId,
        requestText: request,
      });
      setRequestDraft("");
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
    service.setSession(id);
    setSessionId(id);
  }

  const ctx: RunCtx = {
    service,
    sessionId,
    selectSession,
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

      {visibleServiceError && <div className="service-error-banner" role="alert"><span className="service-error-icon"><Icon name="x" size={13} /></span><div><strong>{visibleServiceError.code}</strong><span>{visibleServiceError.message}</span></div><button onClick={() => { setServiceError(null); controller.dismissError(); }} title="Dismiss error" type="button"><Icon name="x" size={13} /></button></div>}

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

      {isSettingsOpen && <GlobalSettings service={service} runs={ctx.runs} onClose={() => setIsSettingsOpen(false)} />}
      {isChatOpen && <GlobalChat ctx={ctx} instant={chatInstant} onClose={() => setIsChatOpen(false)} />}
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
