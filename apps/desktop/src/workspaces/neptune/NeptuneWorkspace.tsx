import { useEffect, useMemo, useState } from "react";
import { SCENES } from "../../app/scenes";
import { WorkspaceFrame } from "../../design-system/WorkspaceFrame";
import AgentComposer from "../../app/AgentComposer";
import { delegateToPlanet } from "../../app/delegate";
import { mediaDelegationRequest } from "./neptuneOps";
import { CapNotInstalled } from "../../design-system/WorkspaceFrame";
import { t, type I18nKey } from "../../app/i18n";
import type { RunCtx } from "../../app/runCtx";

/** Media Studio per hand-drawn spec §10 — type tabs (图像/视频/3D/预览) |
 *  media canvas | right ops | composer. */
const MEDIA = /\.(svg|png|jpe?g|gif|mp4|webm|wav|mp3|glb)$/i;
const KINDS = { image: /\.(svg|png|jpe?g|gif)$/i, video: /\.(mp4|webm)$/i, "3d": /\.glb$/i, all: /.*/ } as const;
const TABS: Record<keyof typeof KINDS, I18nKey> = { image: "nep.tab-image", video: "nep.tab-video", "3d": "nep.tab-3d", all: "nep.tab-all" };

export default function NeptuneWorkspace({ ctx }: { ctx: RunCtx }) {
  const { runs, service } = ctx;
    const scene = SCENES[8];
  const [tab, setTab] = useState<keyof typeof KINDS>("image");
  const [sel, setSel] = useState<string | null>(null);
  const [op, setOp] = useState<{ ok: boolean; text: string } | null>(null);

  const media = useMemo(() => runs.flatMap((r) => r.artifacts.filter((a) => MEDIA.test(a.path)).map((a) => ({ run: r, artifact: a }))), [runs]);
  const visible = media.filter((m) => KINDS[tab].test(m.artifact.path));
  const cur = visible.find((m) => m.artifact.artifactId === sel) ?? visible[0];
  const isSvg = cur?.artifact.path.endsWith(".svg") ?? false;
  const [content, setContent] = useState<{ text: string; stale: boolean; result: string } | null>(null);
  useEffect(() => {
    if (!cur || !isSvg) { setContent(null); return; }
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
  }, [service, cur?.artifact.artifactId, cur?.run.runId, isSvg]);

  return (
    <WorkspaceFrame scene={scene} ctx={ctx}>
      <div className="media">
        <div className="media-main">
          <div className="seg-tabs media-tabs">
            {(Object.keys(KINDS) as (keyof typeof KINDS)[]).map((k) => (
              <button className={tab === k ? "on" : ""} key={k} onClick={() => { setTab(k); setSel(null); }} type="button">{t(TABS[k])}</button>
            ))}
          </div>
          <div className="media-canvas">
            {cur ? (
              isSvg
                ? <div className="media-svg">
                    {content?.stale && <p className="omni-empty">{t("art.stale")}</p>}
                    {content
                      ? content.result !== "ok"
                        ? <p className="omni-empty">{content.result === "missing" ? t("art.missing") : content.result === "unsupported" ? t("art.unsupported") : t("art.preview-failed", { msg: content.text })}</p>
                        : <img alt={cur.artifact.path.split("/").pop()} src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(content.text)}`} />
                      : <p className="omni-empty">…</p>}
                  </div>
                : <div className="empty-state"><p>{cur.artifact.path.split("/").pop()}</p><p className="omni-empty">{cur.artifact.size} — {t("nep.binary-pending")}</p></div>
            ) : <div className="empty-state"><p>{t("nep.no-artifacts", { type: t(TABS[tab]) })}</p></div>}
            {cur && (
              <div className="ven-meta media-meta"><span>{cur.artifact.kind} · {cur.artifact.size}</span>
                <button className="ven-link" onClick={() => ctx.openRun(cur.run.runId, cur.run.sessionId)} title={t("uranus.goto-run")} type="button">{cur.run.runId}</button>
              </div>
            )}
          </div>
          <AgentComposer ctx={ctx} planet="neptune" placeholder={t("nep.composer-ph")} />
        </div>
        <aside className="media-side">
          <h4 className="ws-mod">{t("nep.works")}</h4>
          {visible.map(({ artifact: a }) => (
            <button className={`nep-thumb ${cur?.artifact.artifactId === a.artifactId ? "on" : ""}`} key={a.artifactId} onClick={() => setSel(a.artifactId)} type="button">
              <span className="nep-thumb-kind">{a.path.split(".").pop()?.toUpperCase()}</span>
            </button>
          ))}
          <h4 className="ws-mod">{t("nep.prefs")}</h4>
          {cur && (
            <>
              <button className="btn secondary" onClick={() => {
                setOp(null);
                void delegateToPlanet(ctx, "neptune", mediaDelegationRequest(cur.artifact.path, cur.run.runId))
                  .then(() => setOp({ ok: true, text: t("nep.delegated") }))
                  .catch((e) => setOp({ ok: false, text: String(e instanceof Error ? e.message : e) }));
              }} type="button">{t("nep.render-mp4")}</button>
              {op && <p className="omni-empty">{op.text}</p>}
            </>
          )}
          <CapNotInstalled what={t("nep.cap-gen")} />
          <div className="ws-statusbar"><span>{visible.length} ITEMS</span><span>{t(TABS[tab])}</span><span className="right">neptune</span></div>
        </aside>
      </div>
    </WorkspaceFrame>
  );
}
