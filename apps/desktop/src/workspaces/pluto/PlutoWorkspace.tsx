import { useCallback, useEffect, useState, type FormEvent } from "react";
import { SCENES } from "../../app/scenes";
import { WorkspaceFrame } from "../../design-system/WorkspaceFrame";
import { requestIdForRun } from "../../app/ui";
import type { RunCtx } from "../../app/runCtx";
import type { ApprovalSummary, MotifSummary } from "../../types/service";

/** §5.10 Pluto / Genesis — motif-driven exploration.  Real motif domain:
 *  create → assign (delegates to pluto) → explore → resolve (reports + memory).
 *  Judgement rail stays as pending approvals. */
const GLYPH: Record<string, string> = { mercury: "☿", venus: "♀", earth: "⊕", mars: "♂", jupiter: "♃", saturn: "♄", uranus: "⛢", neptune: "♆", pluto: "♇" };
const STATUS_TAG: Record<string, string> = { open: "待探索", exploring: "探索中", resolved: "已明白" };

export default function PlutoWorkspace({ ctx }: { ctx: RunCtx }) {
  const { service } = ctx;
  const scene = SCENES[9];
  const [motifs, setMotifs] = useState<MotifSummary[]>([]);
  const [approvals, setApprovals] = useState<ApprovalSummary[]>([]);
  const [selMotif, setSelMotif] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [motifDraft, setMotifDraft] = useState("");

  const req = useCallback(() => ({
    tenantId: service.identity.tenantId,
    sessionId: service.identity.sessionId,
    requestId: requestIdForRun(0),
  }), [service]);

  const refresh = useCallback(async () => {
    try {
      const [m, a] = await Promise.all([
        service.listMotifs(req() as never),
        service.listApprovals({ ...req(), status: "PENDING" } as never),
      ]);
      setMotifs(m); setApprovals(a); setError(null);
    } catch (e) { setError(String(e)); }
  }, [service, req]);

  async function onCreateMotif(event: FormEvent) {
    event.preventDefault();
    const text = motifDraft.trim();
    if (!text) return;
    setBusy("create");
    try {
      await service.createMotif({ ...req(), text, source: "user" } as never);
      setMotifDraft(""); await refresh();
    } catch (e) { setError(String(e)); }
    finally { setBusy(null); }
  }

  async function onAssign(m: MotifSummary) {
    if (!ctx.activeRun) { setError("先在 Sun 起个 run 作委派宿主。"); return; }
    setBusy(m.motifId);
    try {
      const d = await service.assignMotif({ ...req(), motifId: m.motifId, parentRunId: ctx.activeRun.runId } as never);
      // child run starts on the delegated task — exploration proceeds
      await service.startRun({
        tenantId: service.identity.tenantId,
        sessionId: `${service.identity.sessionId}:subtask:${d.taskId}`,
        requestId: requestIdForRun(motifs.length + 2),
        requestText: m.text, taskId: d.taskId,
      } as never);
      await refresh();
    } catch (e) { setError(String(e)); }
    finally { setBusy(null); }
  }

  async function onResolve(m: MotifSummary) {
    if (!ctx.activeRun) { setError("需要宿主 run 来记录解析来源。"); return; }
    const resolution = window.prompt("汇报——这轮探索真正搞明白了什么？", m.resolution || "");
    if (!resolution?.trim()) return;
    setBusy(m.motifId);
    try {
      await service.resolveMotif({
        ...req(), motifId: m.motifId, resolutionRunId: ctx.activeRun.runId,
        resolution: resolution.trim(),
      } as never);
      await refresh();
    } catch (e) { setError(String(e)); }
    finally { setBusy(null); }
  }

  useEffect(() => {
    void refresh();
    const t = window.setInterval(() => void refresh(), 10_000);
    return () => window.clearInterval(t);
  }, [refresh]);

  async function decide(a: ApprovalSummary, decision: "approve" | "deny") {
    setBusy(a.approvalId);
    try {
      await service.decideApproval({ ...req(), runId: a.runId, approvalId: a.approvalId, decision, decidedBy: service.identity.userId } as never);
      await refresh();
    } finally { setBusy(null); }
  }

  const curMotif = motifs.find((m) => m.motifId === selMotif) ?? motifs[0];

  return (
    <WorkspaceFrame scene={scene} ctx={ctx}>
      <div className="plu-page">
        {/* 母题 rail — real motif domain */}
        <aside className="plu-motifs">
          <form className="ven-compose" onSubmit={onCreateMotif}>
            <textarea className="ven-compose-input" onChange={(e) => setMotifDraft(e.target.value)}
              placeholder="发放母题——想让它去搞明白什么？" rows={3} value={motifDraft} />
            <button className="btn primary ven-compose-send" disabled={busy === "create" || !motifDraft.trim()} type="submit">
              {busy === "create" ? "发放中…" : "◈ 发放母题"}
            </button>
          </form>
          <h4>母题</h4>
          {motifs.map((m) => (
            <button className={`ura-lib-row ${curMotif?.motifId === m.motifId ? "on" : ""}`} key={m.motifId}
              onClick={() => setSelMotif(m.motifId)} type="button">
              <strong>{m.text.slice(0, 30)}{m.text.length > 30 ? "…" : ""}</strong>
              <small>{STATUS_TAG[m.status] ?? m.status} · {m.source === "auto" ? "自动" : "手动"}</small>
            </button>
          ))}
          {motifs.length === 0 && <p className="omni-empty">还没有母题——发放一个开始探索。</p>}
        </aside>

        {/* motif detail + resolution */}
        <section className="plu-claims">
          {curMotif ? (
            <>
              <h3 className="plu-title">母题：{curMotif.text}</h3>
              <div className="ven-meta"><span>status</span><code>{STATUS_TAG[curMotif.status] ?? curMotif.status}</code></div>
              <div className="ven-meta"><span>source</span><code>{curMotif.source === "auto" ? "自动发放" : "手动发放"}</code></div>
              {curMotif.assignedTaskId && <div className="ven-meta"><span>task</span><code>{curMotif.assignedTaskId}</code></div>}
              {curMotif.resolution && (
                <div className="plu-resolution">
                  <h4>汇报</h4>
                  <p className="doc-p">{curMotif.resolution}</p>
                </div>
              )}
              <div className="ura-graph-actions">
                {curMotif.status === "open" && (
                  <button className="btn primary" disabled={busy === curMotif.motifId} onClick={() => void onAssign(curMotif)} type="button">
                    {busy === curMotif.motifId ? "委派中…" : "▶ 委派探索"}
                  </button>
                )}
                {curMotif.status === "exploring" && (
                  <button className="btn secondary" disabled={busy === curMotif.motifId} onClick={() => void onResolve(curMotif)} type="button">
                    {busy === curMotif.motifId ? "记录中…" : "◉ 记录汇报"}
                  </button>
                )}
              </div>
            </>
          ) : (
            <div className="empty-state"><p>选择或发放一个母题</p></div>
          )}
        </section>

        {/* human judgement */}
        <aside className="plu-judge">
          <h4>人工判断</h4>
          {approvals.length === 0 && <p className="omni-empty">Nothing awaiting judgement.</p>}
          {approvals.map((a) => (
            <div className="plu-pending" key={a.approvalId}>
              <div className="omni-appr-head"><code>{a.riskLevel}</code><strong>{a.action}</strong></div>
              <span className="omni-meta">{a.scope} · {a.runId}</span>
              <div className="omni-appr-actions">
                <button className="btn secondary" disabled={busy === a.approvalId} onClick={() => void decide(a, "approve")} type="button">认可</button>
                <button className="btn danger" disabled={busy === a.approvalId} onClick={() => void decide(a, "deny")} type="button">驳回</button>
              </div>
            </div>
          ))}
          {error && <p className="omni-empty" style={{ color: "var(--st-danger,#ff5a4f)" }}>{error}</p>}
        </aside>
      </div>
    </WorkspaceFrame>
  );
}
