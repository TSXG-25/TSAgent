import type { RunView, WorkflowView } from "../types";
import { Icon } from "./Icon";
import type { InspectorTab } from "./InspectorPanel";
import { submitOnEnter, type RunDraft } from "./runDraft";

interface HomeViewProps {
  runCount: number;
  run: RunView;
  workflow: WorkflowView | undefined;
  runtimeLabel: string;
  draft: RunDraft;
  onOpenRun: () => void;
  onOpenInspector: (tab?: InspectorTab) => void;
}

export function HomeView({ runCount, run, workflow, runtimeLabel, draft, onOpenRun, onOpenInspector }: HomeViewProps) {
  return (
    <div className="home-scroll">
      <div className="home-content">
        <section className="home-hero">
          <span className="home-kicker">RUN ORCHESTRATION · RECOVERY · VERIFICATION</span>
          <div className="home-mark"><Icon name="spark" size={20} /></div>
          <h1>TSAgent <span>Studio</span></h1>
          <p>Local Agent Runtime Console</p>
        </section>

        <section aria-label="TSAgent capabilities" className="dashboard-grid">
          <button className="dashboard-card blue" onClick={onOpenRun} type="button">
            <span className="card-index">01 / RUN CONTROL</span>
            <h2>Run control</h2>
            <p>查看执行状态、Checkpoint 与恢复路径。</p>
            <span className="dashboard-card-footer"><span>{runCount} recent runs</span><Icon name="arrow-up-right" size={14} /></span>
          </button>
          <button className="dashboard-card red" onClick={() => onOpenInspector()} type="button">
            <span className="card-index">02 / WORKFLOW LAB</span>
            <h2>Workflow stages</h2>
            <p>从 Analysis 到 Verification，跟踪每个执行阶段。</p>
            <span className="dashboard-card-footer"><span>{workflow?.progress ?? 0}% current progress</span><Icon name="arrow-up-right" size={14} /></span>
          </button>
          <button className="dashboard-card violet" onClick={() => onOpenInspector("files")} type="button">
            <span className="card-index">03 / ARTIFACT SPACE</span>
            <h2>Artifact workspace</h2>
            <p>查看生成文件、stdout 与验证产物。</p>
            <span className="dashboard-card-footer"><span>{run.artifacts.length} outputs</span><Icon name="arrow-up-right" size={14} /></span>
          </button>
          <button className="dashboard-card green" onClick={() => onOpenInspector("events")} type="button">
            <span className="card-index">04 / VERIFIER</span>
            <h2>Deterministic checks</h2>
            <p>把执行结果收敛为可核验的事实。</p>
            <span className="dashboard-card-footer"><span>{run.verifier.checks}</span><Icon name="arrow-up-right" size={14} /></span>
          </button>
        </section>
      </div>

      <form className="home-composer" onSubmit={draft.onSubmit}>
        <div className="home-composer-top"><span>NEW RUN</span><span>Enter to create an isolated RunContext · Shift+Enter for a new line</span></div>
        <textarea onChange={(event) => draft.onChange(event.target.value)} onKeyDown={submitOnEnter} placeholder="Describe a task for TSAgent…" rows={2} value={draft.value} />
        <div className="home-composer-footer">
          <span><Icon name="spark" size={12} /> {runtimeLabel} · no implicit fallback</span>
          <button className="composer-send" disabled={!draft.value.trim()} type="submit"><span>Create Run</span><Icon name="send" size={13} /></button>
        </div>
      </form>
    </div>
  );
}
