import { useCallback, useEffect, useState } from "react";
import { SCENES } from "../../app/scenes";
import { WorkspaceFrame } from "../../design-system/WorkspaceFrame";
import AgentComposer from "../../app/AgentComposer";
import { requestIdForRun } from "../../app/ui";
import type { RunCtx } from "../../app/runCtx";
import type { EvaluationSummary } from "../../types/service";

/** Organic Intelligence Lab per hand-drawn spec §5 — bio-proxy sliders |
 *  interactive brain/cognitive model | extra params | simulation composer.
 *  Computational analogy — not real physiology. */
const PARAMS = [
  { id: "thyroid", label: "Thyroid hormones", min: 0, max: 100, def: 50 },
  { id: "cortisol", label: "Cortisol", min: 0, max: 100, def: 42 },
  { id: "insulin", label: "Insulin", min: 0, max: 100, def: 58 },
  { id: "leptin", label: "Leptin", min: 0, max: 100, def: 50 },
  { id: "dopamine", label: "Dopamine", min: 0, max: 100, def: 61 },
];

export default function EarthWorkspace({ ctx }: { ctx: RunCtx }) {
  const scene = SCENES[3];
  const [vals, setVals] = useState<Record<string, number>>(Object.fromEntries(PARAMS.map((p) => [p.id, p.def])));
  const [evals, setEvals] = useState<EvaluationSummary[]>([]);
  const [verdict, setVerdict] = useState("keep_candidate");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);

  const refreshEvals = useCallback(async () => {
    try { setEvals(await ctx.service.listEvaluations({ tenantId: ctx.service.identity.tenantId, sessionId: ctx.service.identity.sessionId, requestId: requestIdForRun(0) })); } catch { /* */ }
  }, [ctx.service]);
  useEffect(() => { void refreshEvals(); }, [refreshEvals]);

  async function record() {
    setBusy(true);
    try {
      await ctx.service.recordEvaluation({
        tenantId: ctx.service.identity.tenantId, sessionId: ctx.service.identity.sessionId, requestId: requestIdForRun(0),
        name: `bio-sim-${Date.now().toString(36)}`, runId: ctx.activeRun?.runId ?? "",
        baselineRef: "baseline:defaults", candidateRef: `candidate:${PARAMS.map((p) => `${p.id}=${vals[p.id]}`).join(",")}`,
        verdict, notes: notes.trim() || `params: ${JSON.stringify(vals)}`,
      });
      setNotes(""); await refreshEvals();
    } catch { /* */ } finally { setBusy(false); }
  }
  const left = PARAMS.slice(0, 3), right = PARAMS.slice(3);

  const slider = (p: (typeof PARAMS)[number]) => (
    <div className="bio-param" key={p.id}>
      <div className="bio-param-head"><span>{p.label}</span><code>{vals[p.id]}</code></div>
      <input max={p.max} min={p.min} onChange={(e) => setVals((v) => ({ ...v, [p.id]: Number(e.target.value) }))} type="range" value={vals[p.id]} />
    </div>
  );

  return (
    <WorkspaceFrame scene={scene} ctx={ctx}>
      <div className="lab">
        <aside className="lab-rail">{left.map(slider)}</aside>
        <main className="lab-brain">
          {ctx.activeRun && (
            <div className="ws-hud"><span className="hud-mark">◈</span>
              <div className="ws-hud-info"><strong>{ctx.activeRun.request?.slice(0, 60) || "Organic Lab"}</strong>
                <small><code>{ctx.activeRun.runId}</code> · {ctx.activeRun.status}</small></div>
            </div>
          )}
          <div className="lab-brain-stage">
            <svg viewBox="0 0 200 140" className="lab-brain-svg">
              <ellipse cx="100" cy="72" rx="72" ry="52" fill="none" stroke="var(--planet-accent)" strokeWidth="1.4" opacity=".8" />
              <path d="M100 24v96 M60 44q20 16 0 40 M140 44q-20 16 0 40 M48 70q26 8 52 0 M100 70q26 8 52 0" stroke="var(--planet-accent)" strokeWidth="1" fill="none" opacity=".5" />
              <circle cx="78" cy="60" r="4" fill="var(--planet-accent)" opacity=".7" />
              <circle cx="118" cy="88" r="4" fill="var(--planet-accent)" opacity=".7" />
              <circle cx="126" cy="54" r="3" fill="var(--planet-accent)" opacity=".5" />
            </svg>
            <p className="lab-disclaimer">what simulation? — 计算模拟，非真实生理预测</p>
          </div>
          <AgentComposer ctx={ctx} planet="earth" placeholder="模拟场景 — 调整参数后描述实验…" />
          <section className="lab-eval">
            <div className="ws-mod">实验记录 <em>{evals.length} 次裁决</em></div>
            <div className="lab-eval-form">
              <select onChange={(e) => setVerdict(e.target.value)} value={verdict}>
                <option value="keep_candidate">keep_candidate</option>
                <option value="keep_baseline">keep_baseline</option>
                <option value="inconclusive">inconclusive</option>
              </select>
              <input onChange={(e) => setNotes(e.target.value)} placeholder="结论备注…" value={notes} />
              <button className="btn primary" disabled={busy} onClick={() => void record()} type="button">记录裁决</button>
            </div>
            {evals.slice(0, 6).map((ev) => (
              <div className="lab-eval-row" key={ev.evalId}>
                <strong>{ev.name}</strong><em className={`verd ${ev.verdict}`}>{ev.verdict}</em><small>{ev.notes.slice(0, 48)}</small>
              </div>
            ))}
          </section>
          <div className="ws-statusbar"><span className="sys-ok">● ONLINE</span><span>{PARAMS.length} PARAMS</span><span>{evals.length} EVALS</span><span className="right">earth</span></div>
        </main>
        <aside className="lab-rail">{right.map(slider)}</aside>
      </div>
    </WorkspaceFrame>
  );
}
