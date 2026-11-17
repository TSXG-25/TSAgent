import { useCallback, useEffect, useMemo, useState } from "react";
import { SCENES } from "../../app/scenes";
import { WorkspaceFrame, CapNotInstalled } from "../../design-system/WorkspaceFrame";
import { t } from "../../app/i18n";
import { accessRows, createPreviewGate } from "./jupiterAccess";
import type { RunCtx } from "../../app/runCtx";
import { WorkspaceTasks } from "../../app/WorkspaceTasks";
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
  const [notice, setNotice] = useState<string | null>(null);
  const [preview, setPreview] = useState<string | null>(null);

  const req = useCallback(() => ({ sessionId: ctx.sessionId }), [service, ctx.sessionId]);
  // 预览归属 — 每次请求一个序号，响应提交时仍是最新才落盘；
  // 选中变化即作废在途请求（同一产物的重复请求靠序号区分，不靠对象身份）。
  const previewGate = useMemo(() => createPreviewGate(), []);
  useEffect(() => { previewGate.invalidate(); setPreview(null); }, [previewGate, sel]);
  useEffect(() => {
    service.listFacts(req()).then(setFacts).catch((e) => { setFacts([]); setErr(String(e)); });
    service.searchArtifacts({ ...req(), query: "", allSessions: true }).then(setFiles).catch((e) => setErr(String(e)));
  }, [service, req]);
  // 会话切换 — 选中项/历史属于旧会话，清空后按新 session 重新拉取。
  useEffect(() => { setSel(null); setHistory([]); }, [ctx.sessionId]);

  return (
    <WorkspaceFrame scene={scene} ctx={ctx}>
      <div className="jup">
        <div className="jup-tabs">
          <button className={tab === "memory" ? "on" : ""} onClick={() => setTab("memory")} type="button">{t("jup.tab-memory")}</button>
          <button className={tab === "file" ? "on" : ""} onClick={() => setTab("file")} type="button">{t("jup.tab-file")}</button>
          <button className={tab === "history" ? "on" : ""} onClick={() => { setTab("history"); service.listHistory(req()).then(setHistory).catch((e) => setErr(String(e))); }} type="button">{t("jup.tab-history")}</button>
          <input className="jup-q" onChange={(e) => setQ(e.target.value)} placeholder={t("jup.filter")} value={q} />
        </div>
        <div className="jup-split">
          <section className="jup-list">
            {tab === "memory" ? (
              <>
                <h4 className="ws-mod">{t("jup.facts")} <em>{t("jup.facts-scope")}</em> <button className="jup-cloud-toggle" onClick={() => setCloud((v) => !v)} type="button">{cloud ? t("jup.view-list") : t("jup.view-cloud")}</button></h4>
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
                    <span className="jup-cloud-note">{t("jup.cloud-note", { n: facts.length })}</span>
                  </div>
                )}
                {!cloud && facts.filter((f) => !q || `${f.category}${f.key}`.includes(q)).map((f) => (
                  <span className={`jup-row ${sel === f ? "on" : ""}`} key={f.category + ":" + f.key} onClick={() => setSel(f)}>
                    <strong>{f.category}:{f.key}</strong><small>{f.value.slice(0, 40)}</small>
                    <i className="jup-forget" title={t("jup.forget-title", { scope: f.scope, key: `${f.category}:${f.key}` })} onClick={(e) => { e.stopPropagation(); void service.forgetFact({ ...req(), category: f.category, key: f.key, scope: f.scope }).then(() => service.listFacts(req()).then(setFacts)).then(() => { setNotice(t("jupiter.forgot")); if (sel === f) setSel(null); }).catch((x) => setErr(String(x))); }}>✕</i>
                  </span>
                ))}
                {!cloud && facts.length === 0 && <p className="omni-empty">{t("jup.no-facts")}</p>}
                {!cloud && facts.length > 0 && <p className="omni-empty" style={{ opacity: 0.6 }}>{t("jup.forget-note")}</p>}
              </>
            ) : tab === "history" ? (
              <>
                <h4 className="ws-mod">{t("jup.tab-history")} <em>{t("jup.history-note")}</em></h4>
                {history.filter((h) => !q || h.requestText.includes(q)).map((h) => (
                  <button className="jup-row" key={h.runId}
                    onClick={() => ctx.openRun(h.runId, ctx.sessionId)} type="button">
                    <strong>{h.requestText.slice(0, 42)}</strong>
                    <small>{h.status} · {h.updatedAt.slice(0, 19)}</small>
                    <small>{h.answerText.slice(0, 60)}</small>
                  </button>
                ))}
                {history.length === 0 && <p className="omni-empty">{t("jup.no-history")}</p>}
              </>
            ) : (
              <>
                <h4 className="ws-mod">{t("jup.artifacts")} <em>{t("jup.all-sessions")}</em></h4>
                {files.filter((f) => !q || f.reference.includes(q)).map((f) => (
                  <button className={`jup-row ${sel === f ? "on" : ""}`} key={f.artifactId} onClick={() => setSel(f)} type="button">
                    <strong>{f.reference}</strong><small>{f.runId} · {f.artifactType}</small>
                  </button>
                ))}
              </>
            )}
          </section>
          <aside className="jup-inspect">
            <WorkspaceTasks ctx={ctx} planet="jupiter" />
            <h4 className="ws-mod">{t("jup.access")} <em>{t("jup.grouping")}</em></h4>
            {sel ? (
              <>
                {accessRows(sel).map((row) => (
                  <div className="jup-perm" key={row.label}><span>{row.label}</span><code>{row.value}</code></div>
                ))}
                <pre className="jup-detail">{JSON.stringify(sel, null, 2).slice(0, 900)}</pre>
                {"artifactId" in sel && (
                  <>
                    <button className="btn secondary" onClick={() => {
                      const a = sel as JupiterArtifactSummary;
                      const tok = previewGate.next();
                      void service.readArtifact({ sessionId: a.sessionId, runId: a.runId, artifactId: a.artifactId })
                        .then((c) => { if (previewGate.isCurrent(tok)) setPreview(c.result === "ok" ? c.text.slice(0, 1200) : c.result); })
                        .catch((e) => { if (previewGate.isCurrent(tok)) setPreview(String(e instanceof Error ? e.message : e)); });
                    }} type="button">{t("artifact.preview")}</button>
                    {preview && <pre className="jup-detail">{preview}</pre>}
                  </>
                )}
              </>
            ) : <p className="omni-empty">{t("jup.select-item")}</p>}
            <CapNotInstalled what={t("jup.cap-graph")} />
            {notice && <p className="omni-empty">{notice}</p>}
            {err && <p className="omni-empty" style={{ color: "var(--st-danger,#ff5a4f)" }}>{err}</p>}
          </aside>
        </div>
        <div className="ws-statusbar"><span>{facts.length} FACTS</span><span>{files.length} ARTIFACTS</span><span className="right">jupiter</span></div>
      </div>
    </WorkspaceFrame>
  );
}
