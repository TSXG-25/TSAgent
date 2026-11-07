import type { RunView } from "../types";
import type { DesktopIdentity } from "../types/service";
import { Icon } from "./Icon";
import { statusLabels } from "./StatusBadge";

interface SidebarProps {
  runs: RunView[];
  activeRunId: string;
  runtimeLabel: string;
  identity: DesktopIdentity;
  onNewTask: () => void;
  onSelectRun: (runId: string) => void;
}

export function Sidebar({ runs, activeRunId, runtimeLabel, identity, onNewTask, onSelectRun }: SidebarProps) {
  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <div className="product-lockup">
          <div className="product-mark"><Icon name="spark" size={16} /></div>
          <div className="product-name"><strong>TSAgent</strong><span>Studio</span></div>
        </div>
      </div>

      <button className="new-task-button" onClick={onNewTask} type="button">
        <span className="new-task-icon"><Icon name="plus" size={15} /></span>
        <span>New task</span>
      </button>

      <div className="sidebar-section workspace-section">
        <div className="sidebar-section-title"><span>WORKSPACE</span></div>
        <div className="workspace-row">
          <span className="workspace-icon"><Icon name="folder" size={14} /></span>
          <span><strong>TSAgent Runtime</strong><small>Local workspace</small></span>
        </div>
      </div>

      <div className="sidebar-section runs-section">
        <div className="sidebar-section-title"><span>RECENT RUNS</span><span className="run-count">{runs.length}</span></div>
        <div className="thread-list">
          {runs.map((run) => (
            <button className={`thread-item ${run.runId === activeRunId ? "selected" : ""}`} key={run.runId} onClick={() => onSelectRun(run.runId)} type="button">
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
        <div className="runtime-row"><span className="live-dot" /><span>{runtimeLabel}</span></div>
        <div className="profile-row">
          <span className="profile-avatar">{identity.userId.charAt(0).toUpperCase()}</span>
          <span><strong>{identity.userId}</strong><small>{identity.tenantId}</small></span>
          <Icon name="settings" size={14} />
        </div>
      </div>
    </aside>
  );
}
