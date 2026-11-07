import type { ArtifactView, RunView, StageView, WorkflowView } from "../types";
import { ConversationPanel } from "./ConversationPanel";
import { Icon } from "./Icon";
import { InspectorPanel, type InspectorTab } from "./InspectorPanel";
import type { RunDraft } from "./runDraft";
import { StatusBadge } from "./StatusBadge";

interface RunInspectorProps {
  run: RunView;
  workflow: WorkflowView | undefined;
  selectedStage: StageView | undefined;
  selectedArtifact: ArtifactView | undefined;
  activeTab: InspectorTab;
  canCancel: boolean;
  isCancelling: boolean;
  canResume: boolean;
  isResuming: boolean;
  draft: RunDraft;
  onSelectStage: (stageId: string) => void;
  onSelectArtifact: (artifactId: string) => void;
  onSelectTab: (tab: InspectorTab) => void;
  onCancel: () => void;
  onResume: () => void;
}

function InterruptionSummary({ run }: { run: RunView }) {
  const partialArtifacts = run.artifacts.filter((artifact) => artifact.status === "verified");
  const notExecutedTasks = run.workflows.flatMap((workflow) =>
    workflow.stages.flatMap((stage) =>
      stage.tasks.filter((task) => task.status === "queued" || task.status === "interrupted").map((task) => task.title),
    ),
  );
  return (
    <section aria-label="Interruption summary" className="interruption-summary">
      <div className="interruption-summary-heading">
        <div><span className="panel-eyebrow">PRESERVED PROGRESS</span><h2>{run.status === "cancelled" ? "Cancelled at a safe boundary" : "Timed out at a safe boundary"}</h2></div>
        <StatusBadge status={run.status} />
      </div>
      <div className="interruption-columns">
        <div>
          <strong>Completed before interruption</strong>
          {partialArtifacts.length > 0 ? (
            <ul>{partialArtifacts.map((artifact) => <li key={artifact.artifactId}><Icon name="check" size={12} /><span>{artifact.path}</span><em>VERIFIED</em></li>)}</ul>
          ) : (
            <span className="interruption-empty">No verified artifacts were committed.</span>
          )}
        </div>
        <div>
          <strong>Not executed</strong>
          {notExecutedTasks.length > 0 ? (
            <ul>{notExecutedTasks.slice(0, 5).map((task) => <li key={task}><Icon name="box" size={12} /><span>{task}</span></li>)}</ul>
          ) : (
            <span className="interruption-empty">No pending tasks.</span>
          )}
        </div>
      </div>
    </section>
  );
}

export function RunInspector(props: RunInspectorProps) {
  const { run, workflow, canCancel, isCancelling, canResume, isResuming, onCancel, onResume, onSelectTab } = props;
  const resumeCompleted = run.resume?.outcome === "completed";
  const showCancelling = run.status === "cancelling" || isCancelling;
  return (
    <div className="main-scroll">
      <section className="run-header">
        <div className="run-header-main">
          <div className="run-route"><span>RUN</span><span>/</span><code>{run.runId}</code><StatusBadge status={run.status} /></div>
          <h1>{run.request}</h1>
          <p className="run-subtitle">Created {run.createdAt} <span>·</span> Session <code>{run.sessionId}</code></p>
        </div>
        <div className="run-actions">
          {(canCancel || run.status === "cancelling") && (
            <button aria-label="Cancel Run" className={`cancel-action ${run.status === "cancelling" ? "pending" : ""}`} disabled={!canCancel || isCancelling} onClick={onCancel} type="button">
              <Icon name="x" size={14} /><span>{showCancelling ? "Cancelling…" : "Cancel run"}</span>
            </button>
          )}
          {run.resume && (
            <button className="quiet-action" onClick={() => onSelectTab("events")} type="button"><Icon name="box" size={14} /><span>Recovery checkpoint available</span></button>
          )}
          {run.resume && (
            <button className={`resume-action ${resumeCompleted ? "complete" : ""}`} disabled={!canResume || isResuming} onClick={onResume} type="button">
              <Icon name={resumeCompleted ? "check" : "play"} size={14} />
              <span>{isResuming ? "Requesting resume…" : resumeCompleted ? "Resume · done" : "Resume"}</span>
            </button>
          )}
        </div>
      </section>

      <section aria-label="Run context" className="context-strip">
        <div><span>WORKFLOW</span><strong>{workflow?.workflowId ?? "—"}</strong></div>
        <div><span>PROGRESS</span><strong>{workflow?.progress ?? 0}%</strong></div>
        <div><span>CHECKPOINT</span><strong>{run.resume?.checkpointId ?? "—"}</strong></div>
        <div><span>DURATION</span><strong>{run.duration}</strong></div>
        <div className="context-spacer" />
        <div className="context-boundary"><Icon name="shield" size={13} /><span>Session isolated</span></div>
      </section>

      {(run.status === "cancelled" || run.status === "timed_out") && <InterruptionSummary run={run} />}

      <section className="workbench">
        <ConversationPanel draft={props.draft} run={run} />
        <InspectorPanel
          activeTab={props.activeTab}
          onSelectArtifact={props.onSelectArtifact}
          onSelectStage={props.onSelectStage}
          onSelectTab={onSelectTab}
          run={run}
          selectedArtifact={props.selectedArtifact}
          selectedStage={props.selectedStage}
          workflow={workflow}
        />
      </section>
    </div>
  );
}
