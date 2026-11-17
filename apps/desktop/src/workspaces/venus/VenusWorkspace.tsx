import { useEffect, useMemo, useState } from "react";
import { SCENES } from "../../app/scenes";
import { WorkspaceFrame } from "../../design-system/WorkspaceFrame";
import AgentComposer from "../../app/AgentComposer";
import { draftKey, prefillDraft } from "../../app/draftStore";
import { MarkdownText } from "../../app/ui";
import { t } from "../../app/i18n";
import type { RunCtx } from "../../app/runCtx";
import { useWorkspaceCurrentRun, workspaceRuns } from "../../app/workspaceSelection";
import { WorkspaceTasks } from "../../app/WorkspaceTasks";

/** Muse per hand-drawn spec §4 — File rail + style.md prefs | work preview +
 *  composer | style preset gallery.  Style + preset ride the real
 *  generation request via AgentComposer contextPrefix. */
import { VENUS_PRESETS, styleContext } from "./venusStyle";

export default function VenusWorkspace({ ctx }: { ctx: RunCtx }) {
  const { service } = ctx;
  const scene = SCENES[2];
  const currentRun = useWorkspaceCurrentRun(ctx, "venus");
  const [sel, setSel] = useState<string | null>(null);
  // 风格偏好按 tenant/session/venus 持久化 —— 离开页面不丢。
  const styleKey = `venus-style:${service.identity.tenantId}:${ctx.sessionId}`;
  const DEFAULT_STYLE = "# style.md — 文稿风格偏好\n\n- 语气：冷静、克制\n- 结构：先结论后论证\n- 格式：Markdown";
  const [style, setStyle] = useState(
    () => localStorage.getItem(`${styleKey}:text`) ?? DEFAULT_STYLE,
  );
  const [applied, setApplied] = useState<string | null>(
    () => localStorage.getItem(`${styleKey}:preset`),
  );
  // 切换 session — 重新加载该 session 自己的偏好，不把上一个 session
  // 的 style 写进新键。
  useEffect(() => {
    setStyle(localStorage.getItem(`${styleKey}:text`) ?? DEFAULT_STYLE);
    setApplied(localStorage.getItem(`${styleKey}:preset`));
  }, [styleKey]);
  useEffect(() => { localStorage.setItem(`${styleKey}:text`, style); }, [styleKey, style]);
  useEffect(() => {
    if (applied) localStorage.setItem(`${styleKey}:preset`, applied);
    else localStorage.removeItem(`${styleKey}:preset`);
  }, [styleKey, applied]);

  // 文稿只来自本台在本会话的任务 —— 不混用全局 runs，详情绑定生产 Run。
  const docs = useMemo(() => workspaceRuns(ctx, "venus").flatMap((r) => r.artifacts
    .filter((a) => /\.(md|txt|svg)$/i.test(a.path)).map((a) => ({ run: r, artifact: a }))), [ctx.runs, ctx.sessionId]);
  const cur = docs.find((d) => d.artifact.artifactId === sel) ?? docs[0];
  const [content, setContent] = useState<{ text: string; stale: boolean; result: string } | null>(null);
  useEffect(() => {
    if (!cur) { setContent(null); return; }
    let staleFlag = false;
    setContent(null);
    void service.readArtifact({
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
            <h4 className="ws-mod">{t("ven.files")}</h4>
            {docs.map(({ artifact: a }) => (
              <button className={`muse-file ${cur?.artifact.artifactId === a.artifactId ? "on" : ""}`} key={a.artifactId} onClick={() => setSel(a.artifactId)} type="button">
                📄 {a.path.split("/").pop()}
              </button>
            ))}
            {docs.length === 0 && <p className="omni-empty">{t("ven.no-files")}</p>}
          </section>
          <section className="muse-block muse-style">
            <h4 className="ws-mod">{t("ven.stylistic")} <em>style.md</em></h4>
            <textarea value={style} onChange={(e) => setStyle(e.target.value)} rows={6} />
          </section>
        </aside>

        {/* Work preview + composer */}
        <main className="muse-main">
          {currentRun && (
            <div className="ws-hud"><span className="hud-mark">◈</span>
              <div className="ws-hud-info"><strong>{cur?.artifact.path.split("/").pop() ?? "Muse"}</strong>
                <small><code>{currentRun.runId}</code> · {currentRun.status}</small></div>
            </div>
          )}
          {cur ? (
            <>
              <header className="muse-title"><strong>{cur.artifact.path.split("/").pop()}</strong><code>{cur.run.runId}</code>{applied && <span className="muse-applied">{t("ven.applied", { name: applied })}</span>}
                <button className="ven-link" type="button" onClick={() => prefillDraft(draftKey(service.identity.tenantId, ctx.sessionId, "venus"), t("ven.edit-prefill", { path: cur.artifact.path, runId: cur.run.runId }))}>{t("ven.edit-this")}</button>
              </header>
              <div className="muse-body">
                {content?.stale && <p className="omni-empty">{t("art.stale")}</p>}
                {content && content.result !== "ok" && (
                  <p className="omni-empty">
                    {content.result === "missing" ? t("art.missing") : content.result === "unsupported" ? t("art.unsupported") : t("art.preview-failed", { msg: content.text })}
                  </p>
                )}
                {content
                  ? (/\.md$/i.test(cur.artifact.path)
                    ? <div className="muse-md"><MarkdownText text={content.text} /></div>
                    : <pre>{content.text}</pre>)
                  : <pre>…</pre>}
              </div>
            </>
          ) : <div className="empty-state"><p>{t("ven.select-work")}</p></div>}
          <AgentComposer ctx={ctx} planet="venus" placeholder={t("ven.composer-ph")} contextPrefix={styleContext(style, applied) + (cur ? `\n[文稿引用] ${cur.artifact.path} · run ${cur.run.runId} · artifact ${cur.artifact.artifactId}` : "")} />
        </main>

        {/* Style gallery + applied state */}
        <aside className="muse-right">
          <h4 className="ws-mod">{t("ven.presets")}</h4>
          <div className="muse-presets">
            {VENUS_PRESETS.map((p) => (
              <button className={`muse-preset ${applied === p.id ? "on" : ""}`} key={p.id} onClick={() => setApplied(applied === p.id ? null : p.id)} type="button">
                <span className="muse-swatch" style={{ background: p.swatch }} />
                <strong>{p.name}</strong>
              </button>
            ))}
          </div>
          <div className="muse-reco">
            <h4 className="ws-mod">{t("ven.next")}</h4>
            <p>{applied
              ? t("ven.reco-applied", { name: VENUS_PRESETS.find((p) => p.id === applied)?.name ?? applied })
              : t("ven.reco-none")}</p>
          </div>
                    <WorkspaceTasks ctx={ctx} planet="venus" />
          <div className="ws-statusbar"><span>{docs.length} FILES</span><span>{applied ? `STYLE: ${applied.toUpperCase()}` : "NO STYLE"}</span><span className="right">venus</span></div>
        </aside>
      </div>
    </WorkspaceFrame>
  );
}
