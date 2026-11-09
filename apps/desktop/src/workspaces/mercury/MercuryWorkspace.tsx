import { useCallback, useState, type FormEvent } from "react";
import { SCENES } from "../../app/scenes";
import { WorkspaceFrame } from "../../design-system/WorkspaceFrame";
import AgentComposer from "../../app/AgentComposer";
import { requestIdForRun } from "../../app/ui";
import { CapNotInstalled } from "../../design-system/WorkspaceFrame";
import type { RunCtx } from "../../app/runCtx";
import type { WebSearchResult } from "../../types/service";

/** Navigator per hand-drawn spec §3 — BrowserSessionBar | 4-pane viewport
 *  grid (+/- views) | Plan Action + Action Preview + Stop | Composer.
 *  Views carry real webSearch results until a browser adapter lands. */
type View = { id: number; source: Record<string, unknown> | null; content?: string };

export default function MercuryWorkspace({ ctx }: { ctx: RunCtx }) {
  const { service } = ctx;
  const scene = SCENES[1];
  const [query, setQuery] = useState("");
  const [views, setViews] = useState<View[]>([{ id: 0, source: null }]);
  const [nextId, setNextId] = useState(1);
  const [steps, setSteps] = useState<{ plan: string; preview: string }[]>([]);
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const doSearch = useCallback(async (e: FormEvent) => {
    e.preventDefault();
    const q = query.trim();
    if (!q) return;
    setRunning(true); setErr(null);
    setSteps((s) => [...s, { plan: `web_search → "${q}"`, preview: "searching…" }]);
    try {
      const res = await service.webSearch({
        tenantId: service.identity.tenantId, sessionId: service.identity.sessionId,
        requestId: requestIdForRun(0), query: q, maxResults: 4,
      });
      const srcs = res.sources ?? [];
      setSteps((s) => [...s, { plan: `${srcs.length} sources · ${res.provider}`, preview: srcs[0] ? String(srcs[0].title ?? srcs[0].url ?? "—") : res.content.slice(0, 80) }]);
      setViews((vs) => vs.map((v, i) => ({ ...v, source: srcs[i] ?? v.source, content: i === 0 ? res.content : v.content })));
    } catch (e) { setErr(String(e)); setSteps((s) => [...s, { plan: "failed", preview: String(e) }]); }
    finally { setRunning(false); }
  }, [query, service]);

  return (
    <WorkspaceFrame scene={scene} ctx={ctx}>
      <div className="nav2">
        <div className="nav-main">
          {ctx.activeRun && (
            <div className="ws-hud"><span className="hud-mark">◈</span>
              <div className="ws-hud-info"><strong>{ctx.activeRun.request?.slice(0, 60) || "Navigator"}</strong>
                <small><code>{ctx.activeRun.runId}</code> · {ctx.activeRun.status}</small></div>
            </div>
          )}
          {/* BrowserSessionBar [H] */}
          <form className="nav-bar" onSubmit={doSearch}>
            <span className="nav-web">web:</span>
            <input onChange={(e) => setQuery(e.target.value)} placeholder="search or navigate…" value={query} />
            <button className="btn primary" disabled={running} type="submit">{running ? "…" : "Go"}</button>
          </form>
          {err && <p className="omni-empty" style={{ color: "var(--st-danger,#ff5a4f)" }}>{err}</p>}

          {/* ViewportGrid [H] 2×2 */}
          <div className="nav-grid">
            {views.map((v) => (
              <section className="nav-view" key={v.id}>
                <header><span>View {v.id + 1}</span>{v.source && <code>{String(v.source.source ?? v.source.engine ?? "web")}</code>}</header>
                <div className="nav-view-body">
                  {v.source ? (
                    <>
                      <strong>{String(v.source.title ?? "Source")}</strong>
                      <a href={String(v.source.url ?? "#")} onClick={(e) => e.preventDefault()}>{String(v.source.url ?? "")}</a>
                      <p>{String(v.source.snippet ?? v.content?.slice(0, 240) ?? "")}</p>
                    </>
                  ) : (
                    <div className="empty-state"><p>empty view</p><p className="omni-empty">Search fills this pane</p></div>
                  )}
                </div>
              </section>
            ))}
          </div>

          <div className="nav-foot">
            <div className="nav-vctl">
              <button className="btn secondary" disabled={views.length >= 4} onClick={() => { setViews((v) => [...v, { id: nextId, source: null }]); setNextId((n) => n + 1); }} type="button">+ view</button>
              <button className="btn secondary" disabled={views.length <= 1} onClick={() => setViews((v) => v.slice(0, -1))} type="button">− view</button>
            </div>
            <AgentComposer ctx={ctx} planet="mercury" placeholder="Task for Navigator…" />
          </div>
        </div>

        {/* right rail: Plan Action + Action Preview + Stop */}
        <aside className="nav-side">
          <section className="nav-block">
            <h4 className="ws-mod">Plan Action</h4>
            <div className="step-line">
              {steps.filter((s) => s.plan).map((s, i) => <div className="nav-step" key={i}><code>{i + 1}.</code><span>{s.plan}</span></div>)}
              {steps.length === 0 && <p className="omni-empty">No actions planned.</p>}
            </div>
          </section>
          <section className="nav-block">
            <h4 className="ws-mod">Action Preview</h4>
            {steps.filter((s) => s.preview).slice(-4).map((s, i) => <div className="nav-step" key={i}><code>→</code><span>{s.preview}</span></div>)}
            {steps.length === 0 && <p className="omni-empty">Nothing previewed.</p>}
          </section>
          <section className="nav-block">
            <button className="btn danger" disabled={!running} onClick={() => setRunning(false)} type="button">■ Stop</button>
            <CapNotInstalled what="Browser takeover / screenshots / clicks" />
          </section>
          <div className="ws-statusbar"><span className="sys-ok">● ONLINE</span><span>{views.length} VIEWS</span><span>{steps.length} ACTIONS</span><span className="right">mercury</span></div>
        </aside>
      </div>
    </WorkspaceFrame>
  );
}
