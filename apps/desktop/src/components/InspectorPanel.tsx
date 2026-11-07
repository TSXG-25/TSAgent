import type { ArtifactView, EventTone, RunView, StageStatus, StageView, WorkflowView } from "../types";
import { Icon, type IconName } from "./Icon";

export type InspectorTab = "files" | "events";

const stageLabels: Record<StageStatus, string> = {
  queued: "QUEUED",
  running: "RUNNING",
  completed: "COMPLETED",
  interrupted: "INTERRUPTED",
  verified: "VERIFIED",
  failed: "FAILED",
};

const stageIcons: Record<StageStatus, IconName> = {
  queued: "clock",
  running: "play",
  completed: "check",
  interrupted: "box",
  verified: "check",
  failed: "x",
};

const eventIcons: Record<EventTone, IconName> = {
  info: "activity",
  success: "check",
  warning: "box",
  error: "x",
  neutral: "clock",
};

function isDone(status: StageStatus): boolean {
  return status === "completed" || status === "verified";
}

interface InspectorPanelProps {
  run: RunView;
  workflow: WorkflowView | undefined;
  selectedStage: StageView | undefined;
  selectedArtifact: ArtifactView | undefined;
  activeTab: InspectorTab;
  onSelectStage: (stageId: string) => void;
  onSelectArtifact: (artifactId: string) => void;
  onSelectTab: (tab: InspectorTab) => void;
}

export function InspectorPanel({ run, workflow, selectedStage, selectedArtifact, activeTab, onSelectStage, onSelectArtifact, onSelectTab }: InspectorPanelProps) {
  const progress = workflow?.progress ?? 0;
  const verifier = run.verifier;
  return (
    <aside className="inspector-panel">
      <div className="panel-heading inspector-heading"><div><span className="panel-eyebrow">RUN DETAILS</span><h2>Execution</h2></div><code>{progress}%</code></div>
      <div className="execution-progress">
        <div><span>{workflow?.name ?? "No workflow"}</span><strong>{progress}% complete</strong></div>
        <div className="progress-track"><span style={{ width: `${progress}%` }} /></div>
      </div>

      <div className="stage-list">
        {workflow?.stages.map((stage, index) => (
          <button className={`stage-row ${stage.stageId === selectedStage?.stageId ? "selected" : ""}`} key={stage.stageId} onClick={() => onSelectStage(stage.stageId)} type="button">
            <span className={`stage-marker ${stage.status}`}><Icon name={stageIcons[stage.status]} size={13} /></span>
            <span className="stage-row-copy">
              <span><strong>{stage.name}</strong><em>{stageLabels[stage.status]}</em></span>
              <small>{stage.tasks.filter((task) => isDone(task.status)).length}/{stage.tasks.length} tasks <span>·</span> {stage.duration}</small>
            </span>
            {index < workflow.stages.length - 1 && <i className={`stage-line ${stage.status}`} />}
          </button>
        ))}
      </div>

      {selectedStage && (
        <div className="selected-stage">
          <div className="selected-stage-header"><span>{selectedStage.name} tasks</span><code>{selectedStage.tasks.length}</code></div>
          <div className="task-list">
            {selectedStage.tasks.map((task) => (
              <span className={`task-chip ${task.status}`} key={task.taskId}><Icon name={stageIcons[task.status]} size={10} />{task.title}</span>
            ))}
          </div>
        </div>
      )}

      <div className={`verifier-row ${verifier.status}`}>
        <div className="verifier-icon"><Icon name={verifier.status === "verified" ? "shield" : verifier.status === "failed" ? "x" : "clock"} size={15} /></div>
        <div className="verifier-copy"><span>VERIFIER</span><strong>{verifier.status === "verified" ? "VERIFIED" : verifier.status === "failed" ? "FAILED" : "AWAITING RUN"}</strong></div>
        <div className="verifier-result"><strong>{verifier.checks}</strong><span>stdout <code>{verifier.stdout}</code></span></div>
      </div>

      <div className="inspector-tabs" role="tablist">
        <button aria-selected={activeTab === "files"} className={activeTab === "files" ? "active" : ""} onClick={() => onSelectTab("files")} role="tab" type="button"><Icon name="file" size={13} />Files <span>{run.artifacts.length}</span></button>
        <button aria-selected={activeTab === "events"} className={activeTab === "events" ? "active" : ""} onClick={() => onSelectTab("events")} role="tab" type="button"><Icon name="activity" size={13} />Events <span>{run.events.length}</span></button>
      </div>

      {activeTab === "files" ? (
        <div className="files-view">
          {selectedArtifact ? (
            <>
              <div className="file-list">
                {run.artifacts.map((artifact) => (
                  <button className={`file-row ${artifact.artifactId === selectedArtifact.artifactId ? "selected" : ""}`} key={artifact.artifactId} onClick={() => onSelectArtifact(artifact.artifactId)} type="button">
                    <span className={`file-kind ${artifact.kind}`}><Icon name={artifact.kind === "python" ? "code" : "file"} size={13} /></span>
                    <span><strong>{artifact.path.split("/").pop()}</strong><small>{artifact.path}</small></span>
                    <em className={artifact.status}>{artifact.status === "verified" ? "✓" : artifact.status === "generated" ? "•" : "○"}</em>
                  </button>
                ))}
              </div>
              <div className="file-preview">
                <div><span><Icon name="file" size={12} />{selectedArtifact.path}</span><small>{selectedArtifact.size} · {selectedArtifact.updatedAt}</small></div>
                <div className="artifact-contract-meta">
                  <span>digest <code>{selectedArtifact.digest ?? "—"}</code></span>
                  <span>producer <code>{selectedArtifact.producer ?? "—"}</code></span>
                  <span>revision <code>{selectedArtifact.createdRevision ?? "—"}</code></span>
                </div>
                <pre><code>{selectedArtifact.content}</code></pre>
              </div>
            </>
          ) : (
            <div className="inspector-empty"><Icon name="file" size={18} /><strong>No artifacts yet</strong><span>Generated files will appear here.</span></div>
          )}
        </div>
      ) : (
        <div className="events-view">
          {[...run.events].sort((left, right) => (right.sequenceNumber ?? 0) - (left.sequenceNumber ?? 0)).map((event) => (
            <div className="event-item" key={event.eventId}>
              <div className={`event-marker ${event.tone}`}><Icon name={eventIcons[event.tone]} size={11} /></div>
              <div>
                <div className="event-title"><strong>{event.title}</strong><time>{event.at}</time></div>
                <p>{event.description}</p>
                <code>#{event.sequenceNumber ?? "—"} · {event.type} · rev {event.runRevision ?? "—"}</code>
              </div>
            </div>
          ))}
        </div>
      )}
    </aside>
  );
}
