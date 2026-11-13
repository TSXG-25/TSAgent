import { useEffect, useMemo, useState } from "react";
import { SCENES } from "../../app/scenes";
import { WorkspaceFrame } from "../../design-system/WorkspaceFrame";
import AgentComposer from "../../app/AgentComposer";
import { MarkdownText } from "../../app/ui";
import type { RunCtx } from "../../app/runCtx";

/** Muse per hand-drawn spec §4 — File rail + style.md prefs | work preview +
 *  composer | style preset gallery.  Style + preset ride the real
 *  generation request via AgentComposer contextPrefix. */
import { VENUS_PRESETS, styleContext } from "./venusStyle";

export default function VenusWorkspace({ ctx }: { ctx: RunCtx }) {
  const { runs, service } = ctx;
  const scene = SCENES[2];
  const [sel, setSel] = useState<string | null>(null);
  const [style, setStyle] = useState("# style.md — 文稿风格偏好\n\n- 语气：冷静、克制\n- 结构：先结论后论证\n- 格式：Markdown");
  const [applied, setApplied] = useState<string | null>(null);

  const docs = useMemo(() => runs.flatMap((r) => r.artifacts
    .filter((a) => /\.(md|txt|svg)$/i.test(a.path)).map((a) => ({ run: r, artifact: a }))), [runs]);
  const cur = docs.find((d) => d.artifact.artifactId === sel) ?? docs[0];
  const [content, setContent] = useState<{ text: string; stale: boolean; result: string } | null>(null);
  useEffect(() => {
    if (!cur) { setContent(null); return; }
    let staleFlag = false;
    setContent(null);
    void service.readArtifact({
      tenantId: service.identity.tenantId,
      sessionId: cur.run.sessionId,
      runId: cur.run.runId,
      artifactId: cur.artifact.artifactId,
    }).then((c) => { if (!staleFlag) setContent({ text: c.text, stale: c.stale, result: c.result }); })
      .catch((e) => { if (!staleFlag) setContent({ text: String(e instanceof Error ? e.message : e), stale: false, result: "error" }); });
    return () => { staleFlag = true; };
  }, [service, cur?.artifact.artifactId, cur?.run.runId]);

  return (
    <WorkspaceFrame scene={scene} ctx={ctx}>
      <div className="muse">
        {/* Files + stylistic preferences */}
        <aside className="muse-left">
          <section className="muse-block">
            <h4 className="ws-mod">Files</h4>
            {docs.map(({ artifact: a }) => (
              <button className={`muse-file ${cur?.artifact.artifactId === a.artifactId ? "on" : ""}`} key={a.artifactId} onClick={() => setSel(a.artifactId)} type="button">
                📄 {a.path.split("/").pop()}
              </button>
            ))}
            {docs.length === 0 && <p className="omni-empty">No files yet.</p>}
          </section>
          <section className="muse-block muse-style">
            <h4 className="ws-mod">Stylistic <em>style.md</em></h4>
            <textarea value={style} onChange={(e) => setStyle(e.target.value)} rows={6} />
          </section>
        </aside>

        {/* Work preview + composer */}
        <main className="muse-main">
          {ctx.activeRun && (
            <div className="ws-hud"><span className="hud-mark">◈</span>
              <div className="ws-hud-info"><strong>{cur?.artifact.path.split("/").pop() ?? "Muse"}</strong>
                <small><code>{ctx.activeRun.runId}</code> · {ctx.activeRun.status}</small></div>
            </div>
          )}
          {cur ? (
            <>
              <header className="muse-title"><strong>{cur.artifact.path.split("/").pop()}</strong><code>{cur.run.runId}</code>{applied && <span className="muse-applied">{applied} applied</span>}</header>
              <div className="muse-body">
                {content?.stale && <p className="omni-empty">file changed since recorded</p>}
                {content && content.result !== "ok" && (
                  <p className="omni-empty">
                    {content.result === "missing" ? "file missing" : content.result === "unsupported" ? "unsupported format" : `preview failed: ${content.text}`}
                  </p>
                )}
                {content
                  ? (/\.md$/i.test(cur.artifact.path)
                    ? <div className="muse-md"><MarkdownText text={content.text} /></div>
                    : <pre>{content.text}</pre>)
                  : <pre>…</pre>}
              </div>
            </>
          ) : <div className="empty-state"><p>Select a work</p></div>}
          <AgentComposer ctx={ctx} planet="venus" placeholder="创作指令 — @venus 生成版本…" contextPrefix={styleContext(style, applied)} />
        </main>

        {/* Style gallery + applied state */}
        <aside className="muse-right">
          <h4 className="ws-mod">Presets</h4>
          <div className="muse-presets">
            {VENUS_PRESETS.map((p) => (
              <button className={`muse-preset ${applied === p.id ? "on" : ""}`} key={p.id} onClick={() => setApplied(applied === p.id ? null : p.id)} type="button">
                <span className="muse-swatch" style={{ background: p.swatch }} />
                <strong>{p.name}</strong>
              </button>
            ))}
          </div>
          <div className="muse-reco">
            <h4 className="ws-mod">next composition</h4>
            <p>{applied
              ? `Preset ${VENUS_PRESETS.find((p) => p.id === applied)?.name} and style.md ride the next generation request.`
              : "style.md rides the next generation request; pick a preset to include it."}</p>
          </div>
          <div className="ws-statusbar"><span className="sys-ok">● ONLINE</span><span>{docs.length} FILES</span><span>{applied ? `STYLE: ${applied.toUpperCase()}` : "NO STYLE"}</span><span className="right">venus</span></div>
        </aside>
      </div>
    </WorkspaceFrame>
  );
}
