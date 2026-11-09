import { useCallback, useEffect, useState, type FormEvent } from "react";
import { SCENES } from "../../app/scenes";
import { WorkspaceFrame } from "../../design-system/WorkspaceFrame";
import { requestIdForRun } from "../../app/ui";
import AgentComposer from "../../app/AgentComposer";
import type { RunCtx } from "../../app/runCtx";
import type { MotifSummary } from "../../types/service";

/** Genesis per hand-drawn spec §11 — motif rail (done/发放/current) |
 *  report log (what was done + answer) | 认可/不认可 human judgement. */
const TAG: Record<string, string> = { open: "待探索", exploring: "探索中", resolved: "已完成" };

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
  async function judge(m: MotifSummary, ok: boolean) {
    if (!ok) { setSel(null); return; }
    // 认可 = resolve with prompt for the real reported understanding
    const resolution = window.prompt("汇报——这轮探索真正搞明白了什么？", m.resolution || "");
    if (!resolution?.trim()) return;
    setBusy(true);
    try { await service.resolveMotif({ ...req(), motifId: m.motifId, resolutionRunId: ctx.activeRun?.runId ?? "", resolution: resolution.trim() }); await refresh(); }
    catch (e) { setErr(String(e)); } finally { setBusy(false); }
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
              </div>
              <div className="gen-judge">
                {cur.status === "exploring" && (
                  <button className="btn secondary" disabled={busy} onClick={() => {
                    const r = window.prompt("汇报——这轮探索真正搞明白了什么？", cur.resolution || "");
                    if (r?.trim()) void (async () => { setBusy(true); try { await service.resolveMotif({ ...req(), motifId: cur.motifId, resolutionRunId: ctx.activeRun?.runId ?? "", resolution: r.trim() }); await refresh(); } catch (e) { setErr(String(e)); } finally { setBusy(false); } })();
                  }} type="button">◉ 记录汇报</button>
                )}
                <button className="btn primary" disabled={busy || cur.status === "resolved"} onClick={() => void judge(cur, true)} type="button">认可</button>
                <button className="btn danger" disabled={busy} onClick={() => void judge(cur, false)} type="button">不认可</button>
              </div>
            </>
          ) : <div className="empty-state"><p>Select a motif</p></div>}
          {err && <p className="omni-empty" style={{ color: "var(--st-danger,#ff5a4f)" }}>{err}</p>}
          <AgentComposer ctx={ctx} planet="pluto" placeholder="对当前母题补充指令…" />
          <div className="ws-statusbar"><span className="sys-ok">● ONLINE</span><span>{motifs.length} MOTIFS</span><span>{motifs.filter((m) => m.status === "resolved").length} RESOLVED</span><span className="right">pluto</span></div>
        </main>
      </div>
    </WorkspaceFrame>
  );
}
