import { useCallback, useEffect, useState } from "react";
import { SCENES } from "../../app/scenes";
import { WorkspaceFrame, CapNotInstalled } from "../../design-system/WorkspaceFrame";
import { requestIdForRun } from "../../app/ui";
import type { RunCtx } from "../../app/runCtx";
import type { FactRecordSummary, JupiterArtifactSummary } from "../../types/service";

/** Archive per hand-drawn spec §7 — Memory / File tabs + access/permission
 *  inspector + knowledge graph toggle. Real listFacts/searchArtifacts. */
export default function JupiterWorkspace({ ctx }: { ctx: RunCtx }) {
  const { service } = ctx;
  const scene = SCENES[5];
  const [tab, setTab] = useState<"memory" | "file">("memory");
  const [cloud, setCloud] = useState(true);
  const [facts, setFacts] = useState<FactRecordSummary[]>([]);
  const [files, setFiles] = useState<JupiterArtifactSummary[]>([]);
  const [sel, setSel] = useState<FactRecordSummary | JupiterArtifactSummary | null>(null);
  const [q, setQ] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const req = useCallback(() => ({ tenantId: service.identity.tenantId, sessionId: service.identity.sessionId, requestId: requestIdForRun(0) }), [service]);
  useEffect(() => {
    service.listFacts(req() as never).then(setFacts).catch(() => setFacts([]));
    service.searchArtifacts({ ...req(), query: "", allSessions: true } as never).then(setFiles).catch((e) => setErr(String(e)));
  }, [service, req]);

  return (
    <WorkspaceFrame scene={scene} ctx={ctx}>
      <div className="jup">
        <div className="jup-tabs">
          <button className={tab === "memory" ? "on" : ""} onClick={() => setTab("memory")} type="button">Memory</button>
          <button className={tab === "file" ? "on" : ""} onClick={() => setTab("file")} type="button">File Δ</button>
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
                  <button className={`jup-row ${sel === f ? "on" : ""}`} key={f.category + ":" + f.key} onClick={() => setSel(f)} type="button">
                    <strong>{f.category}:{f.key}</strong><small>{f.value.slice(0, 40)}</small>
                  </button>
                ))}
                {!cloud && facts.length === 0 && <p className="omni-empty">No governed facts.</p>}
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
                <div className="jup-perm"><span>read</span><code>✓</code></div>
                <div className="jup-perm"><span>write</span><code>✓</code></div>
                <div className="jup-perm"><span>execute</span><code>—</code></div>
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
