import { useCallback, useEffect, useState } from "react";
import { SCENES } from "../../app/scenes";
import { WorkspaceFrame, CapNotInstalled } from "../../design-system/WorkspaceFrame";
import { requestIdForRun } from "../../app/ui";
import { accessRows } from "./jupiterAccess";
import type { RunCtx } from "../../app/runCtx";
import type { FactRecordSummary, HistoryEntry, JupiterArtifactSummary } from "../../types/service";

/** Archive per hand-drawn spec §7 — Memory / File tabs + access/permission
 *  inspector + knowledge graph toggle. Real listFacts/searchArtifacts. */
export default function JupiterWorkspace({ ctx }: { ctx: RunCtx }) {
  const { service } = ctx;
  const scene = SCENES[5];
  const [tab, setTab] = useState<"memory" | "file" | "history">("memory");
  const [cloud, setCloud] = useState(true);
  const [facts, setFacts] = useState<FactRecordSummary[]>([]);
  const [files, setFiles] = useState<JupiterArtifactSummary[]>([]);
  const [sel, setSel] = useState<FactRecordSummary | JupiterArtifactSummary | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [q, setQ] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const req = useCallback(() => ({ tenantId: service.identity.tenantId, sessionId: service.identity.sessionId, requestId: requestIdForRun(0) }), [service]);
  useEffect(() => {
    service.listFacts(req()).then(setFacts).catch(() => setFacts([]));
    service.searchArtifacts({ ...req(), query: "", allSessions: true }).then(setFiles).catch((e) => setErr(String(e)));
  }, [service, req]);

  return (
    <WorkspaceFrame scene={scene} ctx={ctx}>
      <div className="jup">
        <div className="jup-tabs">
          <button className={tab === "memory" ? "on" : ""} onClick={() => setTab("memory")} type="button">Memory</button>
          <button className={tab === "file" ? "on" : ""} onClick={() => setTab("file")} type="button">File Δ</button>
          <button className={tab === "history" ? "on" : ""} onClick={() => { setTab("history"); service.listHistory(req()).then(setHistory).catch((e) => setErr(String(e))); }} type="button">History</button>
          <input className="jup-q" onChange={(e) => setQ(e.target.value)} placeholder="filter…" value={q} />
        </div>
        <div className="jup-split">
          <section className="jup-list">
            {tab === "memory" ? (
              <>
                <h4 className="ws-mod">Facts <button className="jup-cloud-toggle" onClick={() => setCloud((v) => !v)} type="button">{cloud ? "list" : "星系"}</button></h4>
                {cloud && (
                  <div className="jup-cloud">
                    {facts.map((f) => {
                      const h = [...(f.category + f.key)].reduce((a, c) => (a * 31 + c.charCodeAt(0)) >>> 0, 7);
                      const x = 8 + (h % 84), y = 8 + ((h >> 8) % 80);
                      const hue = [...f.category].reduce((a, c) => a + c.charCodeAt(0), 0) % 360;
                      const big = f.revision > 3;
                      return (
                        <button className={`jup-star ${sel === f ? "on" : ""}`} key={f.category + ":" + f.key}
                          onClick={() => setSel(f)}
                          style={{ left: `${x}%`, top: `${y}%`, "--star-hue": hue, "--star-size": big ? "5px" : "3.5px" } as React.CSSProperties}
                          title={`${f.category}:${f.key}`} type="button" />
                      );
                    })}
                    <span className="jup-cloud-note">{facts.length} memories · 点击星点查看</span>
                  </div>
                )}
                {!cloud && facts.filter((f) => !q || `${f.category}${f.key}`.includes(q)).map((f) => (
                  <span className={`jup-row ${sel === f ? "on" : ""}`} key={f.category + ":" + f.key} onClick={() => setSel(f)}>
                    <strong>{f.category}:{f.key}</strong><small>{f.value.slice(0, 40)}</small>
                    <i className="jup-forget" title={`forget ${f.category}:${f.key} in scope ${f.scope} — removes this governed fact only; ReMe long-term memory is unaffected`} onClick={(e) => { e.stopPropagation(); void service.forgetFact({ ...req(), category: f.category, key: f.key, scope: f.scope }).then(() => service.listFacts(req()).then(setFacts)).catch((x) => setErr(String(x))); }}>✕</i>
                  </span>
                ))}
                {!cloud && facts.length === 0 && <p className="omni-empty">No governed facts.</p>}
                {!cloud && facts.length > 0 && <p className="omni-empty" style={{ opacity: 0.6 }}>✕ forgets one governed fact in its scope — ReMe long-term memory is governed by its own service.</p>}
              </>
            ) : tab === "history" ? (
              <>
                <h4 className="ws-mod">History <em>本 session 的 run 记录</em></h4>
                {history.filter((h) => !q || h.requestText.includes(q)).map((h) => (
                  <button className="jup-row" key={h.runId}
                    onClick={() => void ctx.controller.refreshRun(h.runId, ctx.sessionId).then(() => ctx.openRun(h.runId)).catch((e) => setErr(String(e)))} type="button">
                    <strong>{h.requestText.slice(0, 42)}</strong>
                    <small>{h.status} · {h.updatedAt.slice(0, 19)}</small>
                    <small>{h.answerText.slice(0, 60)}</small>
                  </button>
                ))}
                {history.length === 0 && <p className="omni-empty">No history.</p>}
              </>
            ) : (
              <>
                <h4 className="ws-mod">Artifacts <em>all sessions</em></h4>
                {files.filter((f) => !q || f.reference.includes(q)).map((f) => (
                  <button className={`jup-row ${sel === f ? "on" : ""}`} key={f.artifactId} onClick={() => setSel(f)} type="button">
                    <strong>{f.reference}</strong><small>{f.runId} · {f.artifactType}</small>
                  </button>
                ))}
              </>
            )}
          </section>
          <aside className="jup-inspect">
            <h4 className="ws-mod">Access <em>grouping</em></h4>
            {sel ? (
              <>
                {accessRows(sel).map((row) => (
                  <div className="jup-perm" key={row.label}><span>{row.label}</span><code>{row.value}</code></div>
                ))}
                <pre className="jup-detail">{JSON.stringify(sel, null, 2).slice(0, 900)}</pre>
              </>
            ) : <p className="omni-empty">Select an item</p>}
            <CapNotInstalled what="Knowledge graph toggle" />
            {err && <p className="omni-empty" style={{ color: "var(--st-danger,#ff5a4f)" }}>{err}</p>}
          </aside>
        </div>
        <div className="ws-statusbar"><span className="sys-ok">● ONLINE</span><span>{facts.length} FACTS</span><span>{files.length} ARTIFACTS</span><span className="right">jupiter</span></div>
      </div>
    </WorkspaceFrame>
  );
}
