import { useCallback, useEffect, useState } from "react";
import { SCENES } from "../../app/scenes";
import { WorkspaceFrame } from "../../design-system/WorkspaceFrame";
import AgentComposer from "../../app/AgentComposer";
import { requestIdForRun } from "../../app/ui";
import { t } from "../../app/i18n";
import type { RunCtx } from "../../app/runCtx";
import { WorkspaceTasks } from "../../app/WorkspaceTasks";
import type { EvaluationSummary, EvaluationVerdict } from "../../types/service";
import { EARTH_PARAMS, EARTH_VERDICTS, buildEvalRequest, composerContext, defaultParamValues, parseRunParams } from "./earthEvalForm";

/** Organic Intelligence Lab per hand-drawn spec §5 — bio-proxy sliders |
 *  interactive brain/cognitive model | extra params | simulation composer.
 *  Computational analogy — not real physiology. */

export default function EarthWorkspace({ ctx }: { ctx: RunCtx }) {
    const scene = SCENES[3];
  const [vals, setVals] = useState<Record<string, number>>(defaultParamValues);
  const [evals, setEvals] = useState<EvaluationSummary[]>([]);
  const [verdict, setVerdict] = useState<EvaluationVerdict>("keep_candidate");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refreshEvals = useCallback(async () => {
    try { setEvals(await ctx.service.listEvaluations({ tenantId: ctx.service.identity.tenantId, sessionId: ctx.service.identity.sessionId, requestId: requestIdForRun(0) })); } catch { /* */ }
  }, [ctx.service]);
  useEffect(() => { void refreshEvals(); }, [refreshEvals]);

  async function record() {
    setBusy(true); setError(null);
    try {
      // 裁决绑定该 Run 实际提交时的参数快照——从 Run 自己的请求文本解析，
      // 而不是当前滑块值；非本 Composer 发起的 Run 参数未知，不伪造。
      await ctx.service.recordEvaluation(buildEvalRequest({
        tenantId: ctx.service.identity.tenantId, sessionId: ctx.service.identity.sessionId,
        requestId: requestIdForRun(0), runId: ctx.activeRun?.runId ?? "",
        verdict, vals: parseRunParams(ctx.activeRun?.request ?? ""), notes: notes.trim(),
      }));
      setNotes(""); await refreshEvals();
    } catch (err) {
      // 失败保留草稿 — 裁决与备注原样留在表单中。
      setError(err instanceof Error ? err.message : String(err));
    } finally { setBusy(false); }
  }
  const left = EARTH_PARAMS.slice(0, 3), right = EARTH_PARAMS.slice(3);

  const slider = (p: (typeof EARTH_PARAMS)[number]) => (
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
            <p className="lab-disclaimer">{t("ear.disclaimer")}</p>
          </div>
          <AgentComposer ctx={ctx} planet="earth" placeholder={t("ear.composer-ph")} contextPrefix={composerContext(vals)} />
          <section className="lab-eval">
            <div className="ws-mod">{t("ear.records")} <em>{t("ear.verdicts", { n: evals.length })}</em></div>
            {(() => {
              const snap = parseRunParams(ctx.activeRun?.request ?? "") ?? {};
              const keys = Object.keys(snap);
              return keys.length > 0 ? (
                <div className="lab-snapshot">
                  <span>{t("earth.submitted-params")}</span>
                  <code>{keys.map((k) => `${k}=${snap[k]}`).join(" ")}</code>
                </div>
              ) : null;
            })()}
            <div className="lab-snapshot lab-snapshot-cur"><span>{t("earth.current-params")}</span><code>{Object.keys(vals).map((k) => `${k}=${vals[k]}`).join(" ")}</code></div>
            <div className="lab-eval-form">
              <select onChange={(e) => setVerdict(e.target.value as EvaluationVerdict)} value={verdict}>
                {EARTH_VERDICTS.map((v) => <option key={v} value={v}>{v}</option>)}
              </select>
              <input onChange={(e) => setNotes(e.target.value)} placeholder={t("ear.notes-ph")} value={notes} />
              <button className="btn primary" disabled={busy} onClick={() => void record()} type="button">{t("ear.record")}</button>
            </div>
            {error && <p className="omni-empty">{error}</p>}
            {evals.slice(0, 6).map((ev) => (
              <div className="lab-eval-row" key={ev.evalId}>
                <strong>{ev.name}</strong><em className={`verd ${ev.verdict}`}>{ev.verdict}</em><small>{ev.notes.slice(0, 48)}</small>
              </div>
            ))}
          </section>
          <div className="ws-statusbar"><span>{EARTH_PARAMS.length} PARAMS</span><span>{evals.length} EVALS</span><span className="right">earth</span></div>
        </main>
        <aside className="lab-rail">
          <WorkspaceTasks ctx={ctx} planet="earth" />
          {right.map(slider)}
        </aside>
      </div>
    </WorkspaceFrame>
  );
}
