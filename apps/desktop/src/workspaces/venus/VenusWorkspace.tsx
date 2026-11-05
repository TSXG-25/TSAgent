import { useEffect, useMemo, useState, type FormEvent } from "react";
import { SCENES } from "../../app/scenes";
import { WorkspaceFrame } from "../../design-system/WorkspaceFrame";
import { requestIdForRun } from "../../app/ui";
import type { RunCtx } from "../../app/runCtx";

/** §5.3 Venus / Muse — documents | ≤68ch reading pane + retrograde version
 *  slider | revision rail. Only document artifacts (.md/.txt). ⌘\ focus. */
export default function VenusWorkspace({ ctx }: { ctx: RunCtx }) {
  const { runs, service } = ctx;
  const scene = SCENES[2];
  const [focus, setFocus] = useState(false);
  const [sel, setSel] = useState<string | null>(null);
  const [compose, setCompose] = useState("");
  const [composing, setComposing] = useState(false);
  const [composeError, setComposeError] = useState<string | null>(null);

  /* Venus compose — delegate a document-generation request to the venus
     workspace; the child run's document artifact lands in the docs list. */
  async function onCompose(event: FormEvent) {
    event.preventDefault();
    const text = compose.trim();
    if (!text || !ctx.activeRun) { setComposeError("先在 Sun 起个 run 作委派宿主。"); return; }
    setComposing(true); setComposeError(null);
    try {
      const d = await service.delegateRun({
        tenantId: service.identity.tenantId, sessionId: service.identity.sessionId,
        requestId: requestIdForRun(runs.length + 1), parentRunId: ctx.activeRun.runId,
        requestText: text, targetPlanet: "venus",
      } as never);
      await service.startRun({
        tenantId: service.identity.tenantId,
        sessionId: `${service.identity.sessionId}:subtask:${d.taskId}`,
        requestId: requestIdForRun(runs.length + 2), requestText: text, taskId: d.taskId,
      } as never);
      setCompose("");
    } catch (e) { setComposeError(e instanceof Error ? e.message : String(e)); }
    finally { setComposing(false); }
  }

  const docs = useMemo(
    () => runs.flatMap((r) => r.artifacts
      .filter((a) => a.path.endsWith(".md") || a.path.endsWith(".txt"))
      .map((a) => ({ run: r, artifact: a }))),
    [runs],
  );
  const cur = docs.find((d) => d.artifact.artifactId === sel) ?? docs[0];

  // retrograde version rail — same path across runs, by createdRevision
  const versions = useMemo(
    () => cur ? docs.filter((d) => d.artifact.path === cur.artifact.path).sort((a, b) => (a.artifact.createdRevision ?? 0) - (b.artifact.createdRevision ?? 0)) : [],
    [docs, cur],
  );
  const curRev = versions.findIndex((d) => d.artifact.artifactId === cur?.artifact.artifactId);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "\\" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); setFocus((f) => !f); } };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const groups = useMemo(() => ({
    drafts: docs.filter((d) => d.artifact.status !== "verified"),
    refs: docs.filter((d) => d.artifact.status === "verified"),
  }), [docs]);

  // minimal markdown → reading typography
  const renderDoc = (text: string) => text.split("\n").map((l, i) => {
    if (l.startsWith("# ")) return <h1 className="doc-h1" key={i}>{l.slice(2)}</h1>;
    if (l.startsWith("## ")) return <h2 className="doc-h2" key={i}>{l.slice(3)}</h2>;
    if (l.startsWith("### ")) return <h3 className="doc-h3" key={i}>{l.slice(4)}</h3>;
    if (l.startsWith("- ") || l.startsWith("* ")) return <li className="doc-li" key={i}>{l.slice(2)}</li>;
    if (/^\s*```/.test(l)) return <code className="doc-fence" key={i}>{l}</code>;
    if (!l.trim()) return <br key={i} />;
    return <p className="doc-p" key={i}>{l}</p>;
  });

  return (
    <WorkspaceFrame scene={scene} ctx={ctx}>
      <div className={`ven-page ${focus ? "focus" : ""}`}>
        {!focus && (
          <aside className="ven-docs">
            <form className="ven-compose" onSubmit={onCompose}>
              <textarea className="ven-compose-input" onChange={(e) => setCompose(e.target.value)}
                placeholder="写一篇…（委派给 Muse 生成文稿）" rows={3} value={compose} />
              <button className="btn primary ven-compose-send" disabled={composing || !compose.trim()} type="submit">
                {composing ? "撰写中…" : "◈ 撰写"}
              </button>
              {composeError && <p className="omni-empty" style={{ color: "var(--st-danger, #ff5a4f)" }}>{composeError}</p>}
            </form>
            <h4>文稿</h4>
            {groups.drafts.map(({ run: r, artifact: a }) => (
              <button className={`ura-lib-row ${cur?.artifact.artifactId === a.artifactId ? "on" : ""}`} key={a.artifactId} onClick={() => setSel(a.artifactId)} type="button">
                <strong>{a.path.split("/").pop()}</strong><small>{r.runId} · rev {a.createdRevision ?? "—"}</small>
              </button>
            ))}
            {groups.refs.length > 0 && <h4>参考</h4>}
            {groups.refs.map(({ run: r, artifact: a }) => (
              <button className={`ura-lib-row ${cur?.artifact.artifactId === a.artifactId ? "on" : ""}`} key={a.artifactId} onClick={() => setSel(a.artifactId)} type="button">
                <strong>{a.path.split("/").pop()}</strong><small>{r.runId} · verified</small>
              </button>
            ))}
            {docs.length === 0 && <p className="omni-empty">No document artifacts yet.</p>}
          </aside>
        )}

        <section className="ven-reader">
          {cur ? (
            <>
              <h2 className="ven-title">{cur.artifact.path.split("/").pop()}</h2>
              <div className="ven-body">{renderDoc(cur.artifact.content ?? "")}</div>
              {versions.length > 1 && (
                <div className="ven-rail">
                  <span className="ven-rail-hint">◀</span>
                  {versions.map((v, i) => (
                    <button className={`ven-rev ${i === curRev ? "on" : ""}`} key={v.artifact.artifactId} onClick={() => setSel(v.artifact.artifactId)} type="button">
                      r{v.artifact.createdRevision ?? i + 1} {i === curRev ? "◉" : "●"}
                    </button>
                  ))}
                  <span className="ven-rail-hint">▶</span>
                </div>
              )}
            </>
          ) : (
            <div className="empty-state"><p>Select a document</p></div>
          )}
        </section>

        {!focus && (
          <aside className="ven-side">
            <h4>来源</h4>
            {cur && (
              <>
                <div className="ven-meta"><span>run</span><code>{cur.run.runId}</code></div>
                <div className="ven-meta"><span>digest</span><code>{cur.artifact.digest?.slice(0, 12) ?? "—"}…</code></div>
                <div className="ven-meta"><span>producer</span><code>{cur.artifact.producer ?? "—"}</code></div>
                <div className="ven-meta"><span>status</span><code>{cur.artifact.status}</code></div>
                <button className="btn secondary ven-export" type="button">导出</button>
              </>
            )}
            <p className="sat-hint">⌘\ 专注模式</p>
          </aside>
        )}
      </div>
    </WorkspaceFrame>
  );
}
