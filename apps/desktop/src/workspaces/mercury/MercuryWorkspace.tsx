import { useCallback, useEffect, useMemo, useReducer, useState, type FormEvent } from "react";
import { SCENES } from "../../app/scenes";
import { WorkspaceFrame } from "../../design-system/WorkspaceFrame";
import AgentComposer from "../../app/AgentComposer";
import { requestIdForRun } from "../../app/ui";
import { t } from "../../app/i18n";
import { CapNotInstalled } from "../../design-system/WorkspaceFrame";
import { useWorkspaceCurrentRun } from "../../app/workspaceSelection";
import type { RunCtx } from "../../app/runCtx";
import { WorkspaceTasks } from "../../app/WorkspaceTasks";
import { MercurySearchSession } from "./searchSession";

/** Navigator per hand-drawn spec §3 — BrowserSessionBar | 4-pane viewport
 *  grid (+/- views) | Plan Action + Action Preview + Stop | Composer.
 *  Views carry real webSearch results until a browser adapter lands. */
export default function MercuryWorkspace({ ctx }: { ctx: RunCtx }) {
  const { service } = ctx;
  const scene = SCENES[1];
  const currentRun = useWorkspaceCurrentRun(ctx, "mercury");
  const [query, setQuery] = useState("");
  const [steps, setSteps] = useState<{ plan: string; preview: string }[]>([]);
  const [, bump] = useReducer((n: number) => n + 1, 0);

  const search = useMemo(
    () => new MercurySearchSession((q) =>
      service.webSearch({
        tenantId: service.identity.tenantId,
        sessionId: ctx.sessionId,
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
    // 切换会话清空本台搜索状态 —— A 的来源/日志不残留到 B。
    search.bind(ctx.sessionId);
    if (search.sessionId === ctx.sessionId) { setSteps([]); setQuery(""); }
    return () => search.stop();
  }, [search, ctx.sessionId]);

  const doSearch = useCallback(async (e: FormEvent) => {
    e.preventDefault();
    const q = query.trim();
    if (!q) return;
    setSteps((s) => [...s, { plan: `web_search → "${q}"`, preview: t("mer.searching") }]);
    const applied = await search.run(q);
    if (!applied) return; // stopped/superseded — no view or log update
    setSteps((s) => [...s, {
      plan: search.status === "error" || search.status === "unavailable"
        ? t("status.failed")
        : `${search.sourceCount} sources · ${search.provider}`,
      preview: search.err
        ?? String(search.panes[0]?.source?.title ?? search.panes[0]?.source?.url ?? "—"),
    }]);
  }, [query, search]);

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
          {/* BrowserSessionBar [H] */}
          <form className="nav-bar" onSubmit={doSearch}>
            <span className="nav-web">web:</span>
            <input onChange={(e) => setQuery(e.target.value)} placeholder={t("mer.placeholder")} value={query} />
            <button className="btn primary" disabled={search.running} type="submit">{search.running ? "…" : t("mer.go")}</button>
          </form>
          {search.err && <p className="omni-empty" style={{ color: "var(--st-danger,#ff5a4f)" }}>{search.err}</p>}
          {search.status === "empty" && <p className="omni-empty">{t("mer.no-results")}</p>}

          {/* ViewportGrid [H] 2×2 */}
          <div className="nav-grid">
            {search.panes.map((v, i) => (
              <section className="nav-view" key={i}>
                <header><span>{t("mer.view-n", { n: i + 1 })}</span>{v.source && <code>{String(v.source.source ?? v.source.engine ?? "web")}</code>}</header>
                <div className="nav-view-body">
                  {v.source ? (
                    <>
                      <strong>{String(v.source.title ?? "Source")}</strong>
                      <a href={String(v.source.url ?? "#")} rel="noreferrer" target="_blank">{String(v.source.url ?? "")}</a>
                      <p>{String(v.source.snippet ?? v.content?.slice(0, 240) ?? "")}</p>
                    </>
                  ) : (
                    <div className="empty-state"><p>{t("mer.empty-view")}</p><p className="omni-empty">{t("mer.empty-hint")}</p></div>
                  )}
                </div>
              </section>
            ))}
          </div>

          <div className="nav-foot">
            <div className="nav-vctl">
              <button className="btn secondary" disabled={search.panes.length >= 4} onClick={() => search.addPane()} type="button">{t("mer.add-view")}</button>
              <button className="btn secondary" disabled={search.panes.length <= 1} onClick={() => search.removePane()} type="button">{t("mer.remove-view")}</button>
            </div>
            <AgentComposer ctx={ctx} planet="mercury" placeholder={t("mer.composer-ph")} />
          </div>
        </div>

        {/* right rail: Plan Action + Action Preview + Stop */}
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
          <section className="nav-block">
            <button className="btn danger" disabled={!search.running} onClick={() => search.stop()} type="button">{t("mer.stop")}</button>
            <CapNotInstalled what={t("mer.cap-browser")} />
          </section>
                    <WorkspaceTasks ctx={ctx} planet="mercury" />
          <div className="ws-statusbar"><span>{search.panes.length} VIEWS</span><span>{steps.length} ACTIONS</span><span className="right">mercury</span></div>
        </aside>
      </div>
    </WorkspaceFrame>
  );
}
