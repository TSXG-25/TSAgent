import { useEffect, useMemo, useRef, useState, type FormEvent, type SVGProps } from "react";
import type { RunEventView, EventTone, RunStatus, RunView, StageStatus } from "./types";
import {
  AgentServiceClientError,
  type AgentServiceClient,
  type ServiceErrorDTO,
} from "./types/service";
import { createAgentServiceClient } from "./service/clientFactory";
import { mergeRunEvents, toRunView } from "./service/viewMapper";
import "./styles.css";

type InspectorTab = "files" | "events";

type IconName =
  | "activity"
  | "arrow-up-right"
  | "box"
  | "check"
  | "chevron-down"
  | "chevron-right"
  | "clock"
  | "code"
  | "copy"
  | "file"
  | "folder"
  | "layers"
  | "play"
  | "plus"
  | "refresh"
  | "search"
  | "send"
  | "settings"
  | "shield"
  | "spark"
  | "terminal"
  | "x";

const iconPaths: Record<IconName, string> = {
  activity: "M3 12h3l2-7 4 14 2-7h7",
  "arrow-up-right": "M7 17 17 7M9 7h8v8",
  box: "m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Z M4 7.5l8 4.5 8-4.5 M12 12v9",
  check: "m5 12 4 4L19 6",
  "chevron-down": "m6 9 6 6 6-6",
  "chevron-right": "m9 6 6 6-6 6",
  clock: "M12 7v5l3 2 M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z",
  code: "m8 9-4 3 4 3M16 9l4 3-4 3M14 5l-4 14",
  copy: "M8 8V5h11v11h-3 M5 8h11v11H5z",
  file: "M6 3h8l4 4v14H6z M14 3v5h5 M9 13h6M9 17h6",
  folder: "M3 6h7l2 2h9v10H3z",
  layers: "m12 3 9 5-9 5-9-5 9-5ZM3 12l9 5 9-5M3 16l9 5 9-5",
  play: "m8 5 11 7-11 7V5Z",
  plus: "M12 5v14M5 12h14",
  refresh: "M20 11a8 8 0 1 0 1 5M20 5v6h-6",
  search: "m20 20-4.5-4.5M10.5 17a6.5 6.5 0 1 1 0-13 6.5 6.5 0 0 1 0 13Z",
  send: "m21 3-7.5 18-3.4-7.1L3 10.5 21 3ZM10.1 13.9 21 3",
  settings: "M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7ZM19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-1.7 1.7-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5v.2h-2.4v-.2a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1L8 17l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H6v-2.4h.2a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.9L7.3 8.6 9 6.9l.1.1a1.7 1.7 0 0 0 1.9.3 1.7 1.7 0 0 0 1-1.5v-.2h2.4v.2a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l-.1-.1 1.7 1.7-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.5 1h.2V14h-.2a1.7 1.7 0 0 0-1.5 1Z",
  shield: "M12 3 20 6v5c0 5.1-3.4 8.7-8 10-4.6-1.3-8-4.9-8-10V6l8-3Z M9 12l2 2 4-4",
  spark: "m12 2 1.6 6.4L20 10l-6.4 1.6L12 18l-1.6-6.4L4 10l6.4-1.6L12 2ZM19 16l.7 2.3L22 19l-2.3.7L19 22l-.7-2.3L16 19l2.3-.7L19 16Z",
  terminal: "M4 5h16v14H4z M7 9l3 3-3 3M12 15h4",
  x: "m6 6 12 12M18 6 6 18",
};

const statusLabels: Record<RunStatus, string> = {
  pending: "PENDING",
  active: "ACTIVE",
  cancelling: "CANCELLING",
  cancelled: "CANCELLED",
  timed_out: "TIMED OUT",
  completed: "COMPLETED",
  failed: "FAILED",
  blocked: "BLOCKED",
};

const stageLabels: Record<StageStatus, string> = {
  queued: "QUEUED",
  running: "RUNNING",
  completed: "COMPLETED",
  interrupted: "INTERRUPTED",
  verified: "VERIFIED",
  failed: "FAILED",
};

const eventIcons: Record<EventTone, IconName> = {
  info: "activity",
  success: "check",
  warning: "box",
  error: "x",
  neutral: "clock",
};

function toServiceError(error: unknown): ServiceErrorDTO {
  if (error instanceof AgentServiceClientError) return error.toDTO();
  return {
    code: "INTERNAL_ERROR",
    message: "Mock AgentService returned an unexpected error.",
    retryable: false,
  };
}

function highestSequence(events: RunEventView[]): number {
  return events.reduce((highest, event) => Math.max(highest, event.sequenceNumber ?? 0), -1);
}

function requestIdForRun(sequenceHint: number): string {
  const randomId = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${sequenceHint}`;
  return `desktop-${randomId}`;
}

function cancellationRequestId(runId: string): string {
  // One logical Run has one cancellation intent. Keeping this stable makes a
  // retry after a lost response idempotent at the AgentService boundary.
  return `cancel-${runId}`;
}

function Icon({ name, size = 16, className = "", ...props }: { name: IconName; size?: number; className?: string } & SVGProps<SVGSVGElement>) {
  return (
    <svg
      aria-hidden="true"
      className={`icon ${className}`}
      fill="none"
      height={size}
      viewBox="0 0 24 24"
      width={size}
      {...props}
    >
      <path d={iconPaths[name]} stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
    </svg>
  );
}

function StatusBadge({ status }: { status: RunStatus }) {
  return <span className={`status-badge ${status}`}>{statusLabels[status]}</span>;
}

function stageIcon(status: StageStatus): IconName {
  if (status === "completed" || status === "verified") return "check";
  if (status === "running") return "play";
  if (status === "interrupted") return "box";
  if (status === "failed") return "x";
  return "clock";
}

function App() {
  const service = useMemo(() => createAgentServiceClient(), []);
  const client: AgentServiceClient = service;
  const lastSequenceRef = useRef<Record<string, number>>({});
  const seenEventIdsRef = useRef<Record<string, Set<string>>>({});
  const [runs, setRuns] = useState<RunView[]>([]);
  const [activeRunId, setActiveRunId] = useState("run-2048");
  const [selectedArtifactId, setSelectedArtifactId] = useState("run-2048.solution");
  const [selectedStageId, setSelectedStageId] = useState("implementation");
  const [activeTab, setActiveTab] = useState<InspectorTab>("files");
  const [view, setView] = useState<"home" | "inspector">("home");
  const [requestDraft, setRequestDraft] = useState("");
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [serviceError, setServiceError] = useState<ServiceErrorDTO | null>(null);
  const [isResuming, setIsResuming] = useState(false);
  const [isCancelling, setIsCancelling] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function loadRuns() {
      try {
        const snapshots = await service.listRuns();
        const views = await Promise.all(
          snapshots.map(async (snapshot) => {
            const locator = { tenantId: snapshot.tenantId, sessionId: snapshot.sessionId, runId: snapshot.runId };
            const [artifacts, events] = await Promise.all([
              client.listArtifacts(locator),
              client.readEvents({ ...locator, afterSequence: -1 }),
            ]);
            return toRunView(snapshot, artifacts, events);
          }),
        );
        if (cancelled) return;
        for (const run of views) {
          lastSequenceRef.current[run.runId] = highestSequence(run.events);
          seenEventIdsRef.current[run.runId] = new Set(run.events.map((event) => event.eventId));
        }
        setRuns(views);
        setActiveRunId((current) => (views.some((run) => run.runId === current) ? current : views[0]?.runId ?? ""));
      } catch (error) {
        if (!cancelled) setServiceError(toServiceError(error));
      }
    }

    void loadRuns();
    return () => {
      cancelled = true;
    };
  }, [client, service]);

  const activeRun = runs.find((run) => run.runId === activeRunId) ?? runs[0];

  // The desktop adapter uses the durable cursor on every refresh. Keeping the
  // hook above the loading return preserves React's hook ordering while still
  // allowing the initial empty catalog to render its loading state.
  useEffect(() => {
    const currentRun = runs.find((run) => run.runId === activeRunId);
    if (!currentRun || (currentRun.status !== "active" && currentRun.status !== "cancelling")) return;

    const timer = globalThis.setInterval(() => {
      void refreshRun(currentRun.runId).catch((error) => setServiceError(toServiceError(error)));
    }, 250);
    return () => globalThis.clearInterval(timer);
  }, [activeRunId, client, runs]);

  if (!activeRun) {
    return <div className="app-loading">{serviceError ? `${serviceError.code}: ${serviceError.message}` : "Loading Mock AgentService…"}</div>;
  }

  const workflow = activeRun.workflows.find((item) => item.workflowId === activeRun.activeWorkflowId) ?? activeRun.workflows[0];
  const selectedStage = workflow?.stages.find((stage) => stage.stageId === selectedStageId) ?? workflow?.stages[0];
  const selectedArtifact = activeRun.artifacts.find((artifact) => artifact.artifactId === selectedArtifactId) ?? activeRun.artifacts[0];
  const canResume = activeRun.status === "blocked" && activeRun.resume?.outcome === "ready";
  const canCancel = activeRun.status === "active";
  const partialArtifacts = activeRun.artifacts.filter((artifact) => artifact.status === "verified");
  const notExecutedTasks = activeRun.workflows.flatMap((item) =>
    item.stages.flatMap((stage) =>
      stage.tasks
        .filter((task) => task.status === "queued" || task.status === "interrupted")
        .map((task) => task.title),
    ),
  );

  function selectRun(runId: string) {
    const nextRun = runs.find((run) => run.runId === runId);
    if (!nextRun) return;

    setActiveRunId(runId);
    setSelectedArtifactId(nextRun.artifacts[0]?.artifactId ?? "");
    setActiveTab("files");
    setView("inspector");

    const nextWorkflow = nextRun.workflows[0];
    setSelectedStageId(
      nextWorkflow?.stages.find((stage) => stage.status === "running" || stage.status === "interrupted")?.stageId ??
        nextWorkflow?.stages[0]?.stageId ??
        "",
    );
  }

  async function refreshRun(runId: string) {
    const currentRun = runs.find((run) => run.runId === runId);
    if (!currentRun) return;

    setServiceError(null);
    const locator = { tenantId: currentRun.tenantId ?? "tenant-local", sessionId: currentRun.sessionId, runId };
    const afterSequence = lastSequenceRef.current[runId] ?? -1;
    const [snapshot, artifacts, incomingEvents] = await Promise.all([
      client.getRun(locator),
      client.listArtifacts(locator),
      client.readEvents({ ...locator, afterSequence }),
    ]);

    const seenEventIds = seenEventIdsRef.current[runId] ?? new Set<string>();
    const unseenEvents = incomingEvents.filter((event) => !seenEventIds.has(event.eventId));
    for (const event of incomingEvents) seenEventIds.add(event.eventId);
    seenEventIdsRef.current[runId] = seenEventIds;
    lastSequenceRef.current[runId] = Math.max(afterSequence, ...incomingEvents.map((event) => event.sequenceNumber));

    setRuns((current) =>
      current.map((run) => {
        if (run.runId !== runId) return run;
        const mergedEvents = mergeRunEvents(run.events, unseenEvents);
        return { ...toRunView(snapshot, artifacts, []), events: mergedEvents };
      }),
    );
  }

  async function refreshActiveRun() {
    try {
      await refreshRun(activeRun.runId);
    } catch (error) {
      setServiceError(toServiceError(error));
    }
  }

  async function cancelActiveRun() {
    if (!canCancel || isCancelling) return;

    try {
      setServiceError(null);
      setIsCancelling(true);
      await client.cancelRun({
        tenantId: activeRun.tenantId ?? "tenant-local",
        sessionId: activeRun.sessionId,
        runId: activeRun.runId,
        requestId: cancellationRequestId(activeRun.runId),
        requestedBy: "desktop-user",
      });
      await refreshRun(activeRun.runId);
    } catch (error) {
      setServiceError(toServiceError(error));
    } finally {
      setIsCancelling(false);
    }
  }

  async function createRun(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    const request = requestDraft.trim() || "生成一个 Python 程序，计算 9 的平方并保存";
    const requestId = requestIdForRun(runs.length + 1);

    try {
      setServiceError(null);
      const handle = await client.startRun({
        tenantId: "tenant-local",
        sessionId: `session-desktop-${Date.now().toString(16)}`,
        requestId,
        requestText: request,
      });
      const locator = { tenantId: handle.tenantId, sessionId: handle.sessionId, runId: handle.runId };
      const [snapshot, artifacts, events] = await Promise.all([
        client.getRun(locator),
        client.listArtifacts(locator),
        client.readEvents({ ...locator, afterSequence: -1 }),
      ]);
      const nextRun = toRunView(snapshot, artifacts, events);
      lastSequenceRef.current[nextRun.runId] = highestSequence(nextRun.events);
      seenEventIdsRef.current[nextRun.runId] = new Set(events.map((item) => item.eventId));

      setRuns((current) => [nextRun, ...current]);
      setActiveRunId(nextRun.runId);
      setSelectedArtifactId("");
      setSelectedStageId("analysis");
      setActiveTab("files");
      setView("inspector");
      setRequestDraft("");
      setIsCreateOpen(false);
    } catch (error) {
      setServiceError(toServiceError(error));
    }
  }

  async function resumeActiveRun() {
    if (!canResume || !activeRun.resume) return;

    try {
      setServiceError(null);
      setIsResuming(true);
      await client.resumeRun({
        tenantId: activeRun.tenantId ?? "tenant-local",
        sessionId: activeRun.sessionId,
        runId: activeRun.runId,
        resumeRequestId: `${activeRun.runId}.resume-request`,
        checkpointId: activeRun.resume.checkpointId,
        action: activeRun.resume.action,
      });
      await refreshRun(activeRun.runId);
    } catch (error) {
      setServiceError(toServiceError(error));
    } finally {
      setIsResuming(false);
    }
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-header">
          <div className="product-lockup">
            <div className="product-mark"><Icon name="spark" size={16} /></div>
            <div className="product-name"><strong>TSAgent</strong><span>Studio</span></div>
          </div>
          <span className="phase-badge">V2.3D-4C</span>
        </div>

        <button className="new-task-button" onClick={() => setIsCreateOpen(true)} type="button">
          <span className="new-task-icon"><Icon name="plus" size={15} /></span>
          <span>New task</span>
          <kbd>⌘N</kbd>
        </button>

        <div className="sidebar-section workspace-section">
          <div className="sidebar-section-title"><span>WORKSPACE</span><button className="sidebar-icon-button" title="Search workspace" type="button"><Icon name="search" size={14} /></button></div>
          <button className="workspace-row" type="button">
            <span className="workspace-icon"><Icon name="folder" size={14} /></span>
            <span><strong>TSAgent Runtime</strong><small>Local workspace</small></span>
            <Icon name="chevron-right" size={13} />
          </button>
        </div>

        <div className="sidebar-section runs-section">
          <div className="sidebar-section-title"><span>RECENT RUNS</span><span className="run-count">{runs.length}</span></div>
          <div className="thread-list">
            {runs.map((run) => (
              <button className={`thread-item ${run.runId === activeRun.runId ? "selected" : ""}`} key={run.runId} onClick={() => selectRun(run.runId)} type="button">
                <span className={`thread-dot ${run.status}`} />
                <span className="thread-copy">
                  <span className="thread-meta"><code>{run.runId}</code><time>{run.updatedAt}</time></span>
                  <span className="thread-request">{run.request}</span>
                </span>
                <span className={`thread-status ${run.status}`}>{statusLabels[run.status]}</span>
              </button>
            ))}
          </div>
        </div>

        <div className="sidebar-footer">
          <div className="runtime-row"><span className="live-dot" /><span>Mock AgentService</span><code>D4C</code></div>
          <div className="profile-row"><span className="profile-avatar">A</span><span><strong>Alex / Developer</strong><small>Personal workspace</small></span><Icon name="settings" size={14} /></div>
        </div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <button className="topbar-brand" onClick={() => setView("home")} type="button">
            <span className="topbar-brand-mark"><Icon name="spark" size={14} /></span>
            <span>TSAgent Studio</span>
          </button>
          <nav aria-label="Console sections" className="topnav">
            <button className={view === "home" ? "active" : ""} onClick={() => setView("home")} type="button">Console</button>
            <button className={view === "inspector" ? "active" : ""} onClick={() => setView("inspector")} type="button">Runs</button>
            <button onClick={() => { setView("inspector"); setActiveTab("files"); }} type="button">Artifacts</button>
            <button onClick={() => { setView("inspector"); setActiveTab("events"); }} type="button">Events</button>
          </nav>
          <div className="topbar-actions"><span className="runtime-pill"><span className="live-dot" /> Mock AgentService · D4C</span><button className="topbar-button" onClick={() => void refreshActiveRun()} title="Replay events from the last cursor" type="button"><Icon name="refresh" size={15} /></button></div>
        </header>

        {serviceError && <div className="service-error-banner" role="alert"><span className="service-error-icon"><Icon name="x" size={13} /></span><div><strong>{serviceError.code}</strong><span>{serviceError.message}</span></div><button onClick={() => setServiceError(null)} title="Dismiss error" type="button"><Icon name="x" size={13} /></button></div>}

        {view === "home" ? <div className="home-scroll">
          <div className="home-content">
            <section className="home-hero">
              <span className="home-kicker">RUN ORCHESTRATION · RECOVERY · VERIFICATION</span>
              <div className="home-mark"><Icon name="spark" size={20} /></div>
              <h1>TSAgent <span>Studio</span></h1>
              <p>Local Agent Runtime Console</p>
            </section>

            <section aria-label="TSAgent capabilities" className="dashboard-grid">
              <button className="dashboard-card blue" onClick={() => selectRun(activeRun.runId)} type="button">
                <span className="card-index">01 / RUN CONTROL</span>
                <h2>Run control</h2>
                <p>查看执行状态、Checkpoint 与恢复路径。</p>
                <span className="dashboard-card-footer"><span>{runs.length} recent runs</span><Icon name="arrow-up-right" size={14} /></span>
              </button>
              <button className="dashboard-card red" onClick={() => { setView("inspector"); setSelectedStageId("implementation"); }} type="button">
                <span className="card-index">02 / WORKFLOW LAB</span>
                <h2>Workflow stages</h2>
                <p>从 Analysis 到 Verification，跟踪每个执行阶段。</p>
                <span className="dashboard-card-footer"><span>{workflow?.progress ?? 0}% current progress</span><Icon name="arrow-up-right" size={14} /></span>
              </button>
              <button className="dashboard-card violet" onClick={() => { setView("inspector"); setActiveTab("files"); }} type="button">
                <span className="card-index">03 / ARTIFACT SPACE</span>
                <h2>Artifact workspace</h2>
                <p>查看生成文件、stdout 与验证产物。</p>
                <span className="dashboard-card-footer"><span>{activeRun.artifacts.length} outputs</span><Icon name="arrow-up-right" size={14} /></span>
              </button>
              <button className="dashboard-card green" onClick={() => { setView("inspector"); setActiveTab("events"); }} type="button">
                <span className="card-index">04 / VERIFIER</span>
                <h2>Deterministic checks</h2>
                <p>把执行结果收敛为可核验的事实。</p>
                <span className="dashboard-card-footer"><span>{activeRun.verifier.checks}</span><Icon name="arrow-up-right" size={14} /></span>
              </button>
            </section>

          </div>

          <form className="home-composer" onSubmit={createRun}>
            <div className="home-composer-top"><span>NEW RUN</span><span>Enter to create an isolated RunContext</span></div>
            <textarea onChange={(event) => setRequestDraft(event.target.value)} placeholder="Describe a task for TSAgent…" rows={2} value={requestDraft} />
            <div className="home-composer-footer"><span><Icon name="spark" size={12} /> Mock Runtime · no external side effects</span><button className="composer-send" type="submit"><span>Create Run</span><Icon name="send" size={13} /></button></div>
          </form>
        </div> : <div className="main-scroll">
          <section className="run-header">
            <div className="run-header-main">
              <div className="run-route"><span>RUN</span><span>/</span><code>{activeRun.runId}</code><StatusBadge status={activeRun.status} /></div>
              <h1>{activeRun.request}</h1>
              <p className="run-subtitle">Created {activeRun.createdAt} <span>·</span> Session <code>{activeRun.sessionId}</code></p>
            </div>
            <div className="run-actions">
              {(canCancel || activeRun.status === "cancelling") && <button aria-label="Cancel Run" className={`cancel-action ${activeRun.status === "cancelling" ? "pending" : ""}`} disabled={!canCancel || isCancelling} onClick={() => void cancelActiveRun()} type="button"><Icon name="x" size={14} /><span>{activeRun.status === "cancelling" || isCancelling ? "Cancelling…" : "Cancel run"}</span></button>}
              {activeRun.resume && <button className="quiet-action" onClick={() => setActiveTab("events")} type="button"><Icon name="box" size={14} /><span>{activeRun.resume.checkpointId}</span></button>}
              {activeRun.resume && <button className={`resume-action ${activeRun.resume.outcome === "completed" ? "complete" : ""}`} disabled={!canResume || isResuming} onClick={() => void resumeActiveRun()} type="button"><Icon name={activeRun.resume.outcome === "completed" ? "check" : "play"} size={14} /><span>{isResuming ? "Requesting resume…" : activeRun.resume.outcome === "completed" ? "Resume exact · done" : "Resume exact"}</span></button>}
            </div>
          </section>

          <section className="context-strip" aria-label="Run context">
            <div><span>WORKFLOW</span><strong>{workflow?.workflowId ?? "—"}</strong></div>
            <div><span>PROGRESS</span><strong>{workflow?.progress ?? 0}%</strong></div>
            <div><span>CHECKPOINT</span><strong>{activeRun.resume?.checkpointId ?? "—"}</strong></div>
            <div><span>DURATION</span><strong>{activeRun.duration}</strong></div>
            <div className="context-spacer" />
            <div className="context-boundary"><Icon name="shield" size={13} /><span>Session isolated</span></div>
          </section>

          {(activeRun.status === "cancelled" || activeRun.status === "timed_out") && <section aria-label="Interruption summary" className="interruption-summary"><div className="interruption-summary-heading"><div><span className="panel-eyebrow">PRESERVED PROGRESS</span><h2>{activeRun.status === "cancelled" ? "Cancelled at a safe boundary" : "Timed out at a safe boundary"}</h2></div><StatusBadge status={activeRun.status} /></div><div className="interruption-columns"><div><strong>Completed before interruption</strong>{partialArtifacts.length > 0 ? <ul>{partialArtifacts.map((artifact) => <li key={artifact.artifactId}><Icon name="check" size={12} /><span>{artifact.path}</span><em>VERIFIED</em></li>)}</ul> : <span className="interruption-empty">No verified artifacts were committed.</span>}</div><div><strong>Not executed</strong>{notExecutedTasks.length > 0 ? <ul>{notExecutedTasks.slice(0, 5).map((task) => <li key={task}><Icon name="box" size={12} /><span>{task}</span></li>)}</ul> : <span className="interruption-empty">No pending tasks.</span>}</div></div></section>}

          <section className="workbench">
            <section className="conversation-panel">
              <div className="panel-heading"><div><span className="panel-eyebrow">THREAD</span><h2>Conversation</h2></div><span className="panel-count">{activeRun.conversation.length} messages</span></div>
              <div className="conversation-scroll">
                <div className="thread-start"><span className="thread-start-icon"><Icon name="layers" size={14} /></span><span>Run created in <code>{activeRun.sessionId}</code></span></div>
                {activeRun.conversation.map((message) => (
                  <article className={`message ${message.role}`} key={message.messageId}>
                    <div className="message-heading"><span className="message-role">{message.role === "user" ? "You" : "TSAgent"}</span><time>{message.at}</time></div>
                    <div className="message-content">{message.content}</div>
                  </article>
                ))}
                {activeRun.resume && <div className={`checkpoint-message ${activeRun.resume.outcome === "completed" ? "complete" : ""}`}><div className="checkpoint-message-icon"><Icon name={activeRun.resume.outcome === "completed" ? "check" : "box"} size={14} /></div><div><strong>{activeRun.resume.outcome === "completed" ? "Resume completed" : "Checkpoint persisted"}</strong><span>{activeRun.resume.checkpointId} <span>·</span> {activeRun.resume.outcome === "completed" ? "RESUME_EXACT committed" : `active stage: ${activeRun.resume.sourceStage}`}</span></div><em>{activeRun.resume.outcome === "completed" ? "DONE" : "RESUMABLE"}</em></div>}
              </div>

              <form className="task-composer" onSubmit={createRun}>
                <textarea onChange={(event) => setRequestDraft(event.target.value)} placeholder="Ask TSAgent to work on a task…" rows={2} value={requestDraft} />
                <div className="composer-footer"><span><Icon name="spark" size={12} /> Creates an isolated RunContext</span><div><kbd>↵</kbd><button className="composer-send" type="submit"><span>Create Run</span><Icon name="send" size={13} /></button></div></div>
              </form>
            </section>

            <aside className="inspector-panel">
              <div className="panel-heading inspector-heading"><div><span className="panel-eyebrow">RUN DETAILS</span><h2>Execution</h2></div><code>{workflow?.progress ?? 0}%</code></div>
              <div className="execution-progress"><div><span>{workflow?.name ?? "No workflow"}</span><strong>{workflow?.progress ?? 0}% complete</strong></div><div className="progress-track"><span style={{ width: `${workflow?.progress ?? 0}%` }} /></div></div>

              <div className="stage-list">
                {workflow?.stages.map((stage, index) => (
                  <button className={`stage-row ${stage.stageId === selectedStage?.stageId ? "selected" : ""}`} key={stage.stageId} onClick={() => setSelectedStageId(stage.stageId)} type="button">
                    <span className={`stage-marker ${stage.status}`}><Icon name={stageIcon(stage.status)} size={13} /></span>
                    <span className="stage-row-copy"><span><strong>{stage.name}</strong><em>{stageLabels[stage.status]}</em></span><small>{stage.tasks.filter((task) => task.status === "completed" || task.status === "verified").length}/{stage.tasks.length} tasks <span>·</span> {stage.duration}</small></span>
                    {index < workflow.stages.length - 1 && <i className={`stage-line ${stage.status}`} />}
                  </button>
                ))}
              </div>

              {selectedStage && <div className="selected-stage"><div className="selected-stage-header"><span>{selectedStage.name} tasks</span><code>{selectedStage.tasks.length}</code></div><div className="task-list">{selectedStage.tasks.map((task) => <span className={`task-chip ${task.status}`} key={task.taskId}><Icon name={task.status === "completed" ? "check" : task.status === "interrupted" ? "box" : task.status === "running" ? "play" : "clock"} size={10} />{task.title}</span>)}</div></div>}

              <div className={`verifier-row ${activeRun.verifier.status}`}><div className="verifier-icon"><Icon name={activeRun.verifier.status === "verified" ? "shield" : activeRun.verifier.status === "failed" ? "x" : "clock"} size={15} /></div><div className="verifier-copy"><span>VERIFIER</span><strong>{activeRun.verifier.status === "verified" ? "VERIFIED" : activeRun.verifier.status === "failed" ? "FAILED" : "AWAITING RUN"}</strong></div><div className="verifier-result"><strong>{activeRun.verifier.checks}</strong><span>stdout <code>{activeRun.verifier.stdout}</code></span></div></div>

              <div className="inspector-tabs" role="tablist"><button className={activeTab === "files" ? "active" : ""} onClick={() => setActiveTab("files")} role="tab" type="button"><Icon name="file" size={13} />Files <span>{activeRun.artifacts.length}</span></button><button className={activeTab === "events" ? "active" : ""} onClick={() => setActiveTab("events")} role="tab" type="button"><Icon name="activity" size={13} />Events <span>{activeRun.events.length}</span></button></div>

              {activeTab === "files" ? <div className="files-view">{selectedArtifact ? <><div className="file-list">{activeRun.artifacts.map((artifact) => <button className={`file-row ${artifact.artifactId === selectedArtifact.artifactId ? "selected" : ""}`} key={artifact.artifactId} onClick={() => setSelectedArtifactId(artifact.artifactId)} type="button"><span className={`file-kind ${artifact.kind}`}><Icon name={artifact.kind === "python" ? "code" : "file"} size={13} /></span><span><strong>{artifact.path.split("/").pop()}</strong><small>{artifact.path}</small></span><em className={artifact.status}>{artifact.status === "verified" ? "✓" : artifact.status === "generated" ? "•" : "○"}</em></button>)}</div><div className="file-preview"><div><span><Icon name="file" size={12} />{selectedArtifact.path}</span><small>{selectedArtifact.size} · {selectedArtifact.updatedAt}</small></div><div className="artifact-contract-meta"><span>digest <code>{selectedArtifact.digest ?? "—"}</code></span><span>producer <code>{selectedArtifact.producer ?? "—"}</code></span><span>revision <code>{selectedArtifact.createdRevision ?? "—"}</code></span></div><pre><code>{selectedArtifact.content}</code></pre></div></> : <div className="inspector-empty"><Icon name="file" size={18} /><strong>No artifacts yet</strong><span>Generated files will appear here.</span></div>}</div> : <div className="events-view">{activeRun.events.slice().sort((left, right) => (right.sequenceNumber ?? 0) - (left.sequenceNumber ?? 0)).map((event) => <div className="event-item" key={event.eventId}><div className={`event-marker ${event.tone}`}><Icon name={eventIcons[event.tone]} size={11} /></div><div><div className="event-title"><strong>{event.title}</strong><time>{event.at}</time></div><p>{event.description}</p><code>#{event.sequenceNumber ?? "—"} · {event.type} · rev {event.runRevision ?? "—"}</code></div></div>)}</div>}
            </aside>
          </section>

        </div>}
      </main>

      {isCreateOpen && <div className="modal-backdrop" onMouseDown={() => setIsCreateOpen(false)}><div className="create-modal" onMouseDown={(event) => event.stopPropagation()}><div className="modal-heading"><div><span className="panel-eyebrow">NEW TASK</span><h2>Create an isolated Run</h2></div><button className="modal-close" onClick={() => setIsCreateOpen(false)} type="button"><Icon name="x" size={16} /></button></div><p>先用 Mock AgentService 建立一个独立的 Run。正式接入后，这里会继续映射到稳定的 Service Contract。</p><form onSubmit={createRun}><label htmlFor="new-run-request">Task</label><textarea autoFocus id="new-run-request" onChange={(event) => setRequestDraft(event.target.value)} placeholder="例如：生成一个 Python 程序，计算 7 的平方并保存" rows={5} value={requestDraft} /><div className="modal-footer"><span>V2.3C · no external side effects</span><button className="composer-send" type="submit"><span>Create Run</span><Icon name="arrow-up-right" size={13} /></button></div></form></div></div>}
    </div>
  );
}

export default App;
