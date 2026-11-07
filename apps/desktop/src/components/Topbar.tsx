import { Icon } from "./Icon";
import type { InspectorTab } from "./InspectorPanel";

export type AppView = "home" | "inspector";

interface TopbarProps {
  view: AppView;
  runtimeLabel: string;
  onHome: () => void;
  onOpenInspector: (tab?: InspectorTab) => void;
  onRefresh: () => void;
}

export function Topbar({ view, runtimeLabel, onHome, onOpenInspector, onRefresh }: TopbarProps) {
  return (
    <header className="topbar">
      <button className="topbar-brand" onClick={onHome} type="button">
        <span className="topbar-brand-mark"><Icon name="spark" size={14} /></span>
        <span>TSAgent Studio</span>
      </button>
      <nav aria-label="Console sections" className="topnav">
        <button className={view === "home" ? "active" : ""} onClick={onHome} type="button">Console</button>
        <button className={view === "inspector" ? "active" : ""} onClick={() => onOpenInspector()} type="button">Runs</button>
        <button onClick={() => onOpenInspector("files")} type="button">Artifacts</button>
        <button onClick={() => onOpenInspector("events")} type="button">Events</button>
      </nav>
      <div className="topbar-actions">
        <span className="runtime-pill"><span className="live-dot" /> {runtimeLabel}</span>
        <button className="topbar-button" onClick={onRefresh} title="Refresh the active Run from the last event cursor" type="button"><Icon name="refresh" size={15} /></button>
      </div>
    </header>
  );
}
