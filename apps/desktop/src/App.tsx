import { useEffect, useState, type FormEvent } from "react";
import type { RunView, WorkflowView } from "./types";
import { createAgentServiceClient } from "./service/clientFactory";
import { RunController, type RunControllerState } from "./features/runs/runController";
import { CreateRunModal } from "./components/CreateRunModal";
import { HomeView } from "./components/HomeView";
import { Icon } from "./components/Icon";
import type { InspectorTab } from "./components/InspectorPanel";
import { RunInspector } from "./components/RunInspector";
import { submitOnEnter, type RunDraft } from "./components/runDraft";
import { Sidebar } from "./components/Sidebar";
import { Topbar, type AppView } from "./components/Topbar";
import "./styles.css";

function newRequestId(): string {
  const suffix = globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  return `desktop-${suffix}`;
}

function activeWorkflow(run: RunView): WorkflowView | undefined {
  return run.workflows.find((workflow) => workflow.workflowId === run.activeWorkflowId) ?? run.workflows[0];
}

/** RunController already projects every operation failure into `state.error`. */
function errorShownByController(): void {}

function App() {
  const [service] = useState(createAgentServiceClient);
  const [controller] = useState(() => new RunController(service));
  const [controllerState, setControllerState] = useState<RunControllerState>(() => controller.getState());
  const [selectedArtifactId, setSelectedArtifactId] = useState("");
  const [selectedStageId, setSelectedStageId] = useState("");
  const [activeTab, setActiveTab] = useState<InspectorTab>("files");
  const [view, setView] = useState<AppView>("home");
  const [requestDraft, setRequestDraft] = useState("");
  const [isCreateOpen, setIsCreateOpen] = useState(false);

  useEffect(() => {
    const unsubscribe = controller.subscribe(setControllerState);
    void controller.initialize();
    return () => {
      unsubscribe();
      controller.dispose();
    };
  }, [controller]);

  const { runs, error, isLoading, operationState } = controllerState;
  const runtimeLabel = service.mode === "local" ? "Local AgentService" : "Mock AgentService";
  const activeRun = runs.find((run) => run.runId === controllerState.activeRunId) ?? runs[0];

  function resetSelection() {
    setSelectedArtifactId("");
    setSelectedStageId("");
  }

  async function createRun(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const requestText = requestDraft.trim();
    if (!requestText) return;
    try {
      await controller.startRun({
        tenantId: service.identity.tenantId,
        sessionId: service.identity.sessionId,
        requestId: newRequestId(),
        requestText,
      });
    } catch {
      return errorShownByController();
    }
    resetSelection();
    setActiveTab("files");
    setView("inspector");
    setRequestDraft("");
    setIsCreateOpen(false);
  }

  const draft: RunDraft = { value: requestDraft, onChange: setRequestDraft, onSubmit: (event) => void createRun(event) };

  if (!activeRun) {
    return (
      <div className="app-loading">
        <div>{error ? `${error.code}: ${error.message}` : isLoading ? `Connecting to ${runtimeLabel}…` : "No runs in this desktop session."}</div>
        {!isLoading && !error && (
          <form className="empty-run-form" onSubmit={draft.onSubmit}>
            <textarea onChange={(event) => setRequestDraft(event.target.value)} onKeyDown={submitOnEnter} placeholder="Describe a task for TSAgent…" rows={3} value={requestDraft} />
            <button className="composer-send" disabled={!requestDraft.trim()} type="submit">Create Run</button>
          </form>
        )}
      </div>
    );
  }

  const run = activeRun;
  const workflow = activeWorkflow(run);
  const selectedStage =
    workflow?.stages.find((stage) => stage.stageId === selectedStageId) ??
    workflow?.stages.find((stage) => stage.status === "running" || stage.status === "interrupted") ??
    workflow?.stages[0];
  const selectedArtifact = run.artifacts.find((artifact) => artifact.artifactId === selectedArtifactId) ?? run.artifacts[0];
  const canResume = run.status === "blocked" && run.resume?.outcome === "ready";
  const canCancel = run.status === "active";

  function openInspector(tab?: InspectorTab) {
    setView("inspector");
    if (tab) setActiveTab(tab);
  }

  function selectRun(runId: string) {
    controller.selectRun(runId);
    resetSelection();
    openInspector("files");
  }

  return (
    <div className="app-shell">
      <Sidebar activeRunId={run.runId} identity={service.identity} onNewTask={() => setIsCreateOpen(true)} onSelectRun={selectRun} runs={runs} runtimeLabel={runtimeLabel} />

      <main className="main-content">
        <Topbar
          onHome={() => setView("home")}
          onOpenInspector={openInspector}
          onRefresh={() => void controller.refreshRun(run.runId).catch(errorShownByController)}
          runtimeLabel={runtimeLabel}
          view={view}
        />

        {error && (
          <div className="service-error-banner" role="alert">
            <span className="service-error-icon"><Icon name="x" size={13} /></span>
            <div><strong>{error.code}</strong><span>{error.message}</span></div>
            <button onClick={() => controller.dismissError()} title="Dismiss error" type="button"><Icon name="x" size={13} /></button>
          </div>
        )}

        {view === "home" ? (
          <HomeView draft={draft} onOpenInspector={openInspector} onOpenRun={() => selectRun(run.runId)} run={run} runCount={runs.length} runtimeLabel={runtimeLabel} workflow={workflow} />
        ) : (
          <RunInspector
            activeTab={activeTab}
            canCancel={canCancel}
            canResume={canResume}
            draft={draft}
            isCancelling={operationState === "cancelling"}
            isResuming={operationState === "resuming"}
            onCancel={() => void controller.cancelRun(run.runId).catch(errorShownByController)}
            onResume={() => void controller.resumeRun(run.runId).catch(errorShownByController)}
            onSelectArtifact={setSelectedArtifactId}
            onSelectStage={setSelectedStageId}
            onSelectTab={setActiveTab}
            run={run}
            selectedArtifact={selectedArtifact}
            selectedStage={selectedStage}
            workflow={workflow}
          />
        )}
      </main>

      {isCreateOpen && <CreateRunModal draft={draft} onClose={() => setIsCreateOpen(false)} runtimeLabel={runtimeLabel} />}
    </div>
  );
}

export default App;
