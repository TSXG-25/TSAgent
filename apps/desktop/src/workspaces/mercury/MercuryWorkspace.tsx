import { useCallback, useEffect, useMemo, useReducer, useState, type FormEvent } from "react";
import { SCENES } from "../../app/scenes";
import { WorkspaceFrame } from "../../design-system/WorkspaceFrame";
import AgentComposer from "../../app/AgentComposer";
import { t } from "../../app/i18n";
import { useWorkspaceCurrentRun } from "../../app/workspaceSelection";
import type { RunEvent } from "../../types/service";
import type { RunCtx } from "../../app/runCtx";
import { WorkspaceTasks } from "../../app/WorkspaceTasks";
import { MercuryWindowBoard } from "./windows";
import BrowserPane from "./BrowserPane";

/** Navigator — live browser windows per the implementation plan.
 *  1 view fills the row, 2 split side-by-side, 3 puts one left and two
 *  stacked right, 4 grid 2×2; expanding promotes one pane to full
 *  width. Windows are real browser documents owned by this session's
 *  workbench scope — the frames are CDP screencast, not cards. */
export default function MercuryWorkspace({ ctx }: { ctx: RunCtx }) {
  const { service } = ctx;
  const scene = SCENES[1];
  const currentRun = useWorkspaceCurrentRun(ctx, "mercury");
  const [query, setQuery] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [steps, setSteps] = useState<{ plan: string; preview: string }[]>([]);
  const [runEvents, setRunEvents] = useState<RunEvent[]>([]);
  const [, bump] = useReducer((n: number) => n + 1, 0);

  // Right rail reads the run's real event stream — tool calls carry
  // their bound view_id, so each action shows the window it drove.
  const runId = currentRun?.runId;
  const runActive = currentRun?.status === "active" || currentRun?.status === "pending";
  useEffect(() => {
    setRunEvents([]);
    if (!runId) return;
    let dead = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let after = 0;
    const tick = async () => {
      try {
        const batch = await service.readEvents({
          sessionId: ctx.sessionId, runId, afterSequence: after, limit: 200,
        });
        if (dead) return;
        if (batch.length) {
          after = Math.max(...batch.map((e) => e.sequenceNumber));
          setRunEvents((prev) => [...prev, ...batch].slice(-80));
        }
      } catch { /* next tick retries */ }
      if (!dead) timer = setTimeout(tick, runActive ? 1200 : 5000);
    };
    void tick();
    return () => { dead = true; if (timer) clearTimeout(timer); };
  }, [service, ctx.sessionId, runId, runActive]);

  const board = useMemo(() => new MercuryWindowBoard(service), [service]);
  // One lifecycle per (board, session): listener must be re-attached on
  // every bind because board.stop() clears it — a stale session's
  // windows otherwise linger with no repaint ever arriving.
  useEffect(() => {
    board.listener = () => bump();
    board.bind(ctx.sessionId);
    void board.refresh();
    return () => board.stop();
  }, [board, ctx.sessionId]);

  const windows = board.windows;
  const count = expandedId ? 1 : windows.length;
  const shown = expandedId
    ? windows.filter((w) => w.summary.viewId === expandedId)
    : windows;

  const doSearch = useCallback(async (e: FormEvent) => {
    e.preventDefault();
    const q = query.trim();
    if (!q) return;
    // Address-bar semantics: a URL navigates the active window (or the
    // first one); anything else opens a new window on the query.
    const url = /^https?:\/\/|^about:|^[\w-]+(\.[\w-]+)+/.test(q)
      ? (/^[\w-]+(\.[\w-]+)+/.test(q) && !/^[a-z]+:/.test(q) ? `https://${q}` : q)
      : "";
    if (url && board.active) {
      setSteps((s) => [...s, { plan: `navigate → ${url}`, preview: "" }]);
      await board.navigate(board.active.summary.viewId, "navigate", url);
    } else {
      const target = url || `https://duckduckgo.com/?q=${encodeURIComponent(q)}`;
      setSteps((s) => [...s, { plan: `open → ${q}`, preview: target }]);
      await board.open(target);
    }
  }, [query, board]);

  return (
    <WorkspaceFrame scene={scene} ctx={ctx}>
      <div className="nav2">
        <div className="nav-main">
          {currentRun && (
            <div className="ws-hud"><span className="hud-mark">◈</span>
              <div className="ws-hud-info"><strong>{currentRun.request?.slice(0, 60) || "Navigator"}</strong>
                <small><code>{currentRun.runId}</code> · {currentRun.status}</small></div>
            </div>
          )}

          <form className="nav-bar" onSubmit={doSearch}>
            <span className="nav-web">web:</span>
            <input onChange={(e) => setQuery(e.target.value)} placeholder={t("mer.placeholder")} value={query} />
            <button className="btn primary" type="submit">{t("mer.go")}</button>
            <button className="btn secondary" disabled={windows.length >= 4} type="button"
              onClick={() => void board.open()}>{t("mer.new-window")}</button>
          </form>
          {board.err && <p className="omni-empty" style={{ color: "var(--st-danger,#ff5a4f)" }}>{board.err}</p>}
          {board.available === false && <p className="omni-empty">{t("mer.windows-na")}</p>}

          <div className={`nav-grid live count-${count}`}>
            {shown.map((w) => (
              <BrowserPane
                key={w.summary.viewId}
                board={board}
                summary={w.summary}
                active={board.activeViewId === w.summary.viewId}
                expanded={expandedId === w.summary.viewId}
                onSelect={() => { board.activeViewId = w.summary.viewId; bump(); }}
                onExpand={() =>
                  setExpandedId((cur) =>
                    cur === w.summary.viewId ? null : w.summary.viewId,
                  )
                }
              />
            ))}
            {windows.length === 0 && board.available !== false && (
              <section className="nav-view">
                <div className="nav-view-body">
                  <div className="empty-state"><p>{t("mer.empty-view")}</p></div>
                </div>
              </section>
            )}
          </div>

          <div className="nav-foot">
            <div className="nav-vctl" />
            {/* Window ids bind only on same-planet flat sends — @planet
                delegation walks the handoff chain and never carries
                browser windows to another workstation. */}
            <AgentComposer
              ctx={ctx}
              planet="mercury"
              placeholder={t("mer.composer-ph")}
              boundViewIds={() => board.boundIds()}
            />
          </div>
        </div>

        <aside className="nav-side">
          <section className="nav-block">
            <h4 className="ws-mod">{t("mer.plan")}</h4>
            <div className="step-line">
              {runEvents
                .filter((e) => e.eventType === "task_started")
                .map((e) => {
                  const tool = String(e.payload.tool ?? e.payload.verb ?? "");
                  const vid = String(e.payload.view_id ?? "");
                  return (
                    <div className="nav-step" key={e.eventId}>
                      <code>{vid ? vid.slice(0, 8) : "·"}</code>
                      <span>{tool || e.eventType}</span>
                    </div>
                  );
                })}
              {steps.filter((s) => s.plan).map((s, i) => <div className="nav-step" key={`m${i}`}><code>ui</code><span>{s.plan}</span></div>)}
              {runEvents.length === 0 && steps.length === 0 && <p className="omni-empty">{t("mer.no-actions")}</p>}
            </div>
          </section>
          <section className="nav-block">
            <h4 className="ws-mod">{t("mer.preview")}</h4>
            {runEvents
              .filter((e) => e.eventType === "task_completed")
              .slice(-4)
              .map((e) => {
                const vid = String(e.payload.view_id ?? "");
                const status = String(e.payload.status ?? "");
                return (
                  <div className="nav-step" key={e.eventId}>
                    <code>{vid ? vid.slice(0, 8) : "→"}</code>
                    <span>{status}{e.payload.error_code ? ` ${String(e.payload.error_code)}` : ""}</span>
                  </div>
                );
              })}
            {runEvents.length === 0 && <p className="omni-empty">{t("mer.nothing-previewed")}</p>}
          </section>
          <WorkspaceTasks ctx={ctx} planet="mercury" />
          <div className="ws-statusbar"><span>{windows.length} VIEWS</span><span>{runEvents.length} EVENTS</span><span className="right">mercury</span></div>
        </aside>
      </div>
    </WorkspaceFrame>
  );
}
