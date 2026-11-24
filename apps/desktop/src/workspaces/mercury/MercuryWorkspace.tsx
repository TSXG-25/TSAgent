import { useCallback, useEffect, useMemo, useReducer, useState, type FormEvent } from "react";
import { SCENES } from "../../app/scenes";
import { WorkspaceFrame } from "../../design-system/WorkspaceFrame";
import AgentComposer from "../../app/AgentComposer";
import { t } from "../../app/i18n";
import { useWorkspaceCurrentRun } from "../../app/workspaceSelection";
import type { RunCtx } from "../../app/runCtx";
import { WorkspaceTasks } from "../../app/WorkspaceTasks";
import { MercuryWindowBoard } from "./windows";
import BrowserPane from "./BrowserPane";
import { submitToPlanet } from "../../app/delegate";

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
  const [, bump] = useReducer((n: number) => n + 1, 0);

  const board = useMemo(() => new MercuryWindowBoard(service), [service]);
  useEffect(() => {
    board.listener = () => bump();
    return () => { board.listener = null; };
  }, [board]);
  // Bind per chat session — window state is session-scoped on the
  // service side; switching sessions leaves nothing behind here.
  useEffect(() => {
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
            {/* Window ids are bound at submission — the snapshot the send
                captures, not whatever is selected later. */}
            <AgentComposer
              ctx={ctx}
              planet="mercury"
              placeholder={t("mer.composer-ph")}
              onSend={async (to, body, files, options) => ({
                runId: await submitToPlanet(ctx, to, body, files, {
                  ...options,
                  viewIds: board.boundIds(),
                }),
                sessionId: ctx.sessionId,
              })}
            />
          </div>
        </div>

        <aside className="nav-side">
          <section className="nav-block">
            <h4 className="ws-mod">{t("mer.plan")}</h4>
            <div className="step-line">
              {steps.filter((s) => s.plan).map((s, i) => <div className="nav-step" key={i}><code>{i + 1}.</code><span>{s.plan}</span></div>)}
              {steps.length === 0 && <p className="omni-empty">{t("mer.no-actions")}</p>}
            </div>
          </section>
          <section className="nav-block">
            <h4 className="ws-mod">{t("mer.preview")}</h4>
            {steps.filter((s) => s.preview).slice(-4).map((s, i) => <div className="nav-step" key={i}><code>→</code><span>{s.preview}</span></div>)}
            {steps.length === 0 && <p className="omni-empty">{t("mer.nothing-previewed")}</p>}
          </section>
          <WorkspaceTasks ctx={ctx} planet="mercury" />
          <div className="ws-statusbar"><span>{windows.length} VIEWS</span><span>{steps.length} ACTIONS</span><span className="right">mercury</span></div>
        </aside>
      </div>
    </WorkspaceFrame>
  );
}
