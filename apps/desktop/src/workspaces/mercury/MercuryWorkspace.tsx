import { useCallback, useEffect, useMemo, useReducer, useState, type FormEvent } from "react";
import { SCENES } from "../../app/scenes";
import { WorkspaceFrame } from "../../design-system/WorkspaceFrame";
import AgentComposer from "../../app/AgentComposer";
import { requestIdForRun } from "../../app/ui";
import { CapNotInstalled } from "../../design-system/WorkspaceFrame";
import type { RunCtx } from "../../app/runCtx";
import { MercurySearchSession } from "./searchSession";

/** Navigator per hand-drawn spec §3 — BrowserSessionBar | 4-pane viewport
 *  grid (+/- views) | Plan Action + Action Preview + Stop | Composer.
 *  Views carry real webSearch results until a browser adapter lands. */
export default function MercuryWorkspace({ ctx }: { ctx: RunCtx }) {
  const { service } = ctx;
  const scene = SCENES[1];
  const [query, setQuery] = useState("");
  const [steps, setSteps] = useState<{ plan: string; preview: string }[]>([]);
  const [, bump] = useReducer((n: number) => n + 1, 0);

  const search = useMemo(
    () => new MercurySearchSession((q) =>
      service.webSearch({
        tenantId: service.identity.tenantId,
        sessionId: service.identity.sessionId,
        requestId: requestIdForRun(0),
        query: q,
        maxResults: 4,
      }),
    ),
    [service],
  );
  useEffect(() => {
    search.listener = () => bump();
    return () => { search.listener = null; };
  }, [search]);
  // 会话切换与组件卸载都让在途响应失效 — 旧会话的晚到响应不得
  // 更新当前页面（service 对象在会话间复用，memo 不会重建）。
  useEffect(() => {
    search.bind(service.identity.sessionId);
    return () => search.stop();
  }, [search, service.identity.sessionId]);

  const doSearch = useCallback(async (e: FormEvent) => {
    e.preventDefault();
    const q = query.trim();
    if (!q) return;
    setSteps((s) => [...s, { plan: `web_search → "${q}"`, preview: "searching…" }]);
    const applied = await search.run(q);
    if (!applied) return; // stopped/superseded — no view or log update
    setSteps((s) => [...s, {
      plan: search.status === "error" || search.status === "unavailable"
        ? "failed"
        : `${search.sourceCount} sources · ${search.provider}`,
      preview: search.err
        ?? String(search.panes[0]?.source?.title ?? search.panes[0]?.source?.url ?? "—"),
    }]);
  }, [query, search]);

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
            <button className="btn primary" disabled={search.running} type="submit">{search.running ? "…" : "Go"}</button>
          </form>
          {search.err && <p className="omni-empty" style={{ color: "var(--st-danger,#ff5a4f)" }}>{search.err}</p>}
          {search.status === "empty" && <p className="omni-empty">no results</p>}

          {/* ViewportGrid [H] 2×2 */}
          <div className="nav-grid">
            {search.panes.map((v, i) => (
              <section className="nav-view" key={i}>
                <header><span>View {i + 1}</span>{v.source && <code>{String(v.source.source ?? v.source.engine ?? "web")}</code>}</header>
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
              <button className="btn secondary" disabled={search.panes.length >= 4} onClick={() => search.addPane()} type="button">+ view</button>
              <button className="btn secondary" disabled={search.panes.length <= 1} onClick={() => search.removePane()} type="button">− view</button>
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
            <button className="btn danger" disabled={!search.running} onClick={() => search.stop()} type="button">■ Stop</button>
            <CapNotInstalled what="Browser takeover / screenshots / clicks" />
          </section>
          <div className="ws-statusbar"><span className="sys-ok">● ONLINE</span><span>{search.panes.length} VIEWS</span><span>{steps.length} ACTIONS</span><span className="right">mercury</span></div>
        </aside>
      </div>
    </WorkspaceFrame>
  );
}
