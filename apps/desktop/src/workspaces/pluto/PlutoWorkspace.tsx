import { useCallback, useEffect, useState, type FormEvent } from "react";
import { SCENES } from "../../app/scenes";
import { WorkspaceFrame } from "../../design-system/WorkspaceFrame";
import { requestIdForRun } from "../../app/ui";
import AgentComposer from "../../app/AgentComposer";
import { t, type I18nKey } from "../../app/i18n";
import type { RunCtx } from "../../app/runCtx";
import { WorkspaceTasks } from "../../app/WorkspaceTasks";
import type { MotifSummary } from "../../types/service";
import type { RunView } from "../../types";

/** Genesis per hand-drawn spec §11 — motif rail (done/发放/current) |
 *  report log (what was done + answer) | 认可/不认可 human judgement. */
const TAG: Record<string, I18nKey> = { open: "plu.tag-open", exploring: "plu.tag-exploring", reported: "plu.tag-reported", resolved: "plu.tag-resolved" };

export default function PlutoWorkspace({ ctx }: { ctx: RunCtx }) {
  const { service } = ctx;
    const scene = SCENES[9];
  const [motifs, setMotifs] = useState<MotifSummary[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  // "auto" 开关已移除 —— source 如实记录提交来源，没有自动探索流程。
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const req = useCallback(() => ({ tenantId: service.identity.tenantId, sessionId: service.identity.sessionId, requestId: requestIdForRun(0) }), [service]);
  const refresh = useCallback(async () => {
    try { setMotifs(await service.listMotifs(req())); setErr(null); } catch (e) { setErr(String(e)); }
  }, [service, req]);
  useEffect(() => { void refresh(); const t = setInterval(() => void refresh(), 8000); return () => clearInterval(t); }, [refresh]);

  const cur = motifs.find((m) => m.motifId === sel) ?? motifs[0];
  const done = motifs.filter((m) => m.status === "resolved");

  async function create(e: FormEvent) {
    e.preventDefault();
    if (!draft.trim()) return;
    setBusy(true);
    try { await service.createMotif({ ...req(), text: draft.trim(), source: "user" }); setDraft(""); await refresh(); }
    catch (e) { setErr(String(e)); } finally { setBusy(false); }
  }
  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    try { await fn(); await refresh(); } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  }

  /** 委派入口 — assignMotif 返回授权的子 Run 启动配置，立即启动探索。 */
  async function assign(m: MotifSummary) {
    await act(async () => {
      const parent = ctx.activeRun && ctx.activeRun.sessionId === ctx.sessionId ? ctx.activeRun : null;
      const r = await service.assignMotif({
        tenantId: service.identity.tenantId,
        sessionId: ctx.sessionId,
        requestId: requestIdForRun(0),
        motifId: m.motifId,
        parentRunId: parent?.runId ?? "",
      });
      await ctx.controller.startRun({
        tenantId: service.identity.tenantId,
        sessionId: ctx.sessionId,
        requestId: requestIdForRun(0),
        requestText: r.requestText ?? `[母题探索] ${m.text}`,
        taskId: r.taskId,
        workspaceRoot: r.workspaceRoot,
        metadata: { delegation_depth: 1, agent_profile: r.agentProfile ?? {} },
      });
    });
  }

  /** 汇报 — 探索 Run 报告结论（run 证据），等待人工裁决。
   *  证据只能来自该母题 assignedTaskId 的探索 Run 列表，不是全局选中任务。 */
  const [evRuns, setEvRuns] = useState<RunView[]>([]);
  const [evId, setEvId] = useState("");
  useEffect(() => {
    setEvRuns([]); setEvId("");
    if (!cur?.assignedTaskId) return;
    let alive = true;
    void service.getTask({ tenantId: service.identity.tenantId, sessionId: ctx.sessionId, requestId: requestIdForRun(0), taskId: cur.assignedTaskId })
      .then(async (task) => {
        const views = (await Promise.all(task.runIds.map((id) => ctx.controller.refreshRun(id, task.sessionId)))).filter((v): v is RunView => Boolean(v));
        if (alive) setEvRuns(views);
      })
      .catch((e) => setErr(String(e)));
    return () => { alive = false; };
  }, [cur?.assignedTaskId, cur?.motifId]);

  async function report(m: MotifSummary) {
    if (!evId) { setErr(t("plu.no-evidence")); return; }
    const resolution = window.prompt(t("plu.prompt-report"), m.resolution || "");
    if (!resolution?.trim()) return;
    await act(() => service.reportMotif({
      ...req(), motifId: m.motifId,
      resolutionRunId: evId,
      resolution: resolution.trim(),
    }));
  }

  /** 裁决 — 认可把已汇报的结论收进记忆；不认可退回探索，持久可核对。 */
  async function judge(m: MotifSummary, ok: boolean) {
    if (!ok) { await act(() => service.rejectMotif({ ...req(), motifId: m.motifId })); return; }
    const resolution = window.prompt(t("plu.prompt-resolve"), m.resolution || "");
    if (!resolution?.trim()) return;
    await act(() => service.resolveMotif({
      ...req(), motifId: m.motifId,
      resolutionRunId: m.resolutionRunId,
      resolution: resolution.trim(),
    }));
  }


  return (
    <WorkspaceFrame scene={scene} ctx={ctx}>
      <div className="gen">
        <aside className="gen-motifs">
          <section className="gen-block">
            <h4 className="ws-mod">{t("plu.done")}</h4>
            {done.map((m) => <button className="gen-row" key={m.motifId} onClick={() => setSel(m.motifId)} type="button">✓ {m.text.slice(0, 24)}</button>)}
            {done.length === 0 && <p className="omni-empty">—</p>}
          </section>
          <section className="gen-block">
            <h4 className="ws-mod">{t("plu.now")}</h4>
            {motifs.filter((m) => m.status !== "resolved").map((m) => (
              <button className={`gen-row ${cur?.motifId === m.motifId ? "on" : ""}`} key={m.motifId} onClick={() => setSel(m.motifId)} type="button">
                {t(TAG[m.status] ?? "plu.tag-open")} · {m.text.slice(0, 24)}
              </button>
            ))}
          </section>
          <form className="gen-issue" onSubmit={create}>
            <textarea onChange={(e) => setDraft(e.target.value)} placeholder={t("plu.issue-ph")} rows={3} value={draft} />
            <div className="gen-issue-row">
              <button className="btn primary" disabled={busy || !draft.trim()} type="submit">{t("plu.issue")}</button>
              {/* source 如实为 "user" —— 不存在自动探索，不提供误导开关 */}
            </div>
          </form>
        </aside>

        <main className="gen-report">
          <div className="ws-hud"><span className="hud-mark">◈</span>
            <div className="ws-hud-info"><strong>{t("plu.report-log")}</strong>
              <small>{cur ? `${cur.motifId} · ${t(TAG[cur.status] ?? "plu.tag-open")}` : t("plu.no-motif")}</small></div>
          </div>
          {cur ? (
            <>
              <div className="gen-report-body">
                <div className="ven-meta"><span>{t("plu.label-motif")}</span><code>{cur.text}</code></div>
                <div className="ven-meta"><span>{t("plu.label-status")}</span><code>{t(TAG[cur.status] ?? "plu.tag-open")}</code></div>
                {cur.assignedTaskId && <div className="ven-meta"><span>{t("plu.label-task")}</span><code>{cur.assignedTaskId}</code></div>}
                {cur.resolution && <div className="gen-answer"><h4>{t("plu.answer-head")}</h4><p>{cur.resolution}</p></div>}
                {cur.status === "resolved" && (
                  <div className="gen-ingested">
                    <span>{t("pluto.ingested")}</span>
                    {cur.resolutionRunId && <button className="ven-link" onClick={() => ctx.openRun(cur.resolutionRunId, cur.resolutionSessionId || undefined)} title={t("pluto.resolution-run")} type="button">{cur.resolutionRunId}</button>}
                  </div>
                )}
              </div>
              <div className="gen-judge">
                {cur.status === "open" && (
                  <button className="btn secondary" disabled={busy} onClick={() => void assign(cur)} type="button">{t("plu.assign")}</button>
                )}
                {cur.status === "exploring" && (
                  <div className="gen-evidence">
                    <label>{t("plu.evidence")}</label>
                    <select value={evId} onChange={(e) => setEvId(e.target.value)}>
                      <option value="">{t("plu.evidence-pick")}</option>
                      {evRuns.map((r) => <option key={r.runId} value={r.runId}>{r.runId} · {r.status}</option>)}
                    </select>
                    <button className="btn secondary" disabled={busy || !evId} onClick={() => void report(cur)} type="button">{t("plu.report-btn")}</button>
                  </div>
                )}
                <button className="btn primary" disabled={busy || cur.status !== "reported"} onClick={() => void judge(cur, true)} type="button">{t("plu.approve")}</button>
                <button className="btn danger" disabled={busy || cur.status !== "reported"} onClick={() => void judge(cur, false)} type="button">{t("plu.reject")}</button>
              </div>
            </>
          ) : <div className="empty-state"><p>{t("plu.select")}</p></div>}
          {err && <p className="omni-empty" style={{ color: "var(--st-danger,#ff5a4f)" }}>{err}</p>}
          <AgentComposer ctx={ctx} planet="pluto" placeholder={t("plu.composer-ph")} />
                    <WorkspaceTasks ctx={ctx} planet="pluto" />
          <div className="ws-statusbar"><span>{motifs.length} MOTIFS</span><span>{motifs.filter((m) => m.status === "resolved").length} RESOLVED</span><span className="right">pluto</span></div>
        </main>
      </div>
    </WorkspaceFrame>
  );
}
