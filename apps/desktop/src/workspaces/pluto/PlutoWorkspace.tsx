import { useCallback, useEffect, useState, type FormEvent } from "react";
import { SCENES } from "../../app/scenes";
import { WorkspaceFrame } from "../../design-system/WorkspaceFrame";
import { requestIdForRun } from "../../app/ui";
import AgentComposer from "../../app/AgentComposer";
import { t } from "../../app/i18n";
import type { RunCtx } from "../../app/runCtx";
import type { MotifSummary } from "../../types/service";

/** Genesis per hand-drawn spec §11 — motif rail (done/发放/current) |
 *  report log (what was done + answer) | 认可/不认可 human judgement. */
const TAG: Record<string, string> = { open: "待探索", exploring: "探索中", reported: "待裁决", resolved: "已完成" };

export default function PlutoWorkspace({ ctx }: { ctx: RunCtx }) {
  const { service } = ctx;
    const scene = SCENES[9];
  const [motifs, setMotifs] = useState<MotifSummary[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [autoSrc, setAutoSrc] = useState(false);
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
    try { await service.createMotif({ ...req(), text: draft.trim(), source: autoSrc ? "auto" : "user" }); setDraft(""); await refresh(); }
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

  /** 汇报 — 探索 Run 报告结论（run 证据），等待人工裁决。 */
  async function report(m: MotifSummary) {
    const resolution = window.prompt("汇报——这轮探索真正搞明白了什么？", m.resolution || "");
    if (!resolution?.trim()) return;
    await act(() => service.reportMotif({
      ...req(), motifId: m.motifId,
      resolutionRunId: ctx.activeRun?.runId ?? "",
      resolution: resolution.trim(),
    }));
  }

  /** 裁决 — 认可把已汇报的结论收进记忆；不认可退回探索，持久可核对。 */
  async function judge(m: MotifSummary, ok: boolean) {
    if (!ok) { await act(() => service.rejectMotif({ ...req(), motifId: m.motifId })); return; }
    const resolution = window.prompt("认可这份汇报——收进记忆的结论：", m.resolution || "");
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
            <h4 className="ws-mod">已完成母题</h4>
            {done.map((m) => <button className="gen-row" key={m.motifId} onClick={() => setSel(m.motifId)} type="button">✓ {m.text.slice(0, 24)}</button>)}
            {done.length === 0 && <p className="omni-empty">—</p>}
          </section>
          <section className="gen-block">
            <h4 className="ws-mod">此刻母题</h4>
            {motifs.filter((m) => m.status !== "resolved").map((m) => (
              <button className={`gen-row ${cur?.motifId === m.motifId ? "on" : ""}`} key={m.motifId} onClick={() => setSel(m.motifId)} type="button">
                {TAG[m.status]} · {m.text.slice(0, 24)}
              </button>
            ))}
          </section>
          <form className="gen-issue" onSubmit={create}>
            <textarea onChange={(e) => setDraft(e.target.value)} placeholder="发放母题——想让它去搞明白什么？" rows={3} value={draft} />
            <div className="gen-issue-row">
              <button className="btn primary" disabled={busy || !draft.trim()} type="submit">◈ 发放</button>
              <label className="gen-auto"><input checked={autoSrc} onChange={(e) => setAutoSrc(e.target.checked)} type="checkbox" />自动</label>
            </div>
          </form>
        </aside>

        <main className="gen-report">
          <div className="ws-hud"><span className="hud-mark">◈</span>
            <div className="ws-hud-info"><strong>汇报日志</strong>
              <small>{cur ? `${cur.motifId} · ${TAG[cur.status]}` : "no motif"}</small></div>
          </div>
          {cur ? (
            <>
              <div className="gen-report-body">
                <div className="ven-meta"><span>motif</span><code>{cur.text}</code></div>
                <div className="ven-meta"><span>status</span><code>{TAG[cur.status]}</code></div>
                {cur.assignedTaskId && <div className="ven-meta"><span>task</span><code>{cur.assignedTaskId}</code></div>}
                {cur.resolution && <div className="gen-answer"><h4>汇报（含做了什么）</h4><p>{cur.resolution}</p></div>}
                {cur.status === "resolved" && (
                  <div className="gen-ingested">
                    <span>{t("pluto.ingested")}</span>
                    {cur.resolutionRunId && <button className="ven-link" onClick={() => ctx.openRun(cur.resolutionRunId)} title={t("pluto.resolution-run")} type="button">{cur.resolutionRunId}</button>}
                  </div>
                )}
              </div>
              <div className="gen-judge">
                {cur.status === "open" && (
                  <button className="btn secondary" disabled={busy} onClick={() => void assign(cur)} type="button">◈ 委派探索</button>
                )}
                {cur.status === "exploring" && (
                  <button className="btn secondary" disabled={busy} onClick={() => void report(cur)} type="button">◉ 记录汇报</button>
                )}
                <button className="btn primary" disabled={busy || cur.status !== "reported"} onClick={() => void judge(cur, true)} type="button">认可</button>
                <button className="btn danger" disabled={busy || cur.status !== "reported"} onClick={() => void judge(cur, false)} type="button">不认可</button>
              </div>
            </>
          ) : <div className="empty-state"><p>Select a motif</p></div>}
          {err && <p className="omni-empty" style={{ color: "var(--st-danger,#ff5a4f)" }}>{err}</p>}
          <AgentComposer ctx={ctx} planet="pluto" placeholder="对当前母题补充指令…" />
          <div className="ws-statusbar"><span>{motifs.length} MOTIFS</span><span>{motifs.filter((m) => m.status === "resolved").length} RESOLVED</span><span className="right">pluto</span></div>
        </main>
      </div>
    </WorkspaceFrame>
  );
}
