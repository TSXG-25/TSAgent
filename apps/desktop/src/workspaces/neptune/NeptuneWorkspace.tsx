import { useEffect, useMemo, useState } from "react";
import { SCENES } from "../../app/scenes";
import { WorkspaceFrame } from "../../design-system/WorkspaceFrame";
import AgentComposer from "../../app/AgentComposer";
import { delegateToPlanet } from "../../app/delegate";
import { mediaDelegationRequest } from "./neptuneOps";
import { CapNotInstalled } from "../../design-system/WorkspaceFrame";
import { t, type I18nKey } from "../../app/i18n";
import type { RunCtx } from "../../app/runCtx";
import { WorkspaceTasks } from "../../app/WorkspaceTasks";
import { workspaceRuns } from "../../app/workspaceSelection";
import type { ArtifactPreview } from "../../types/service";

/** Media Studio per hand-drawn spec §10 — type tabs | media canvas | ops.
 *  Previews come from the service's guarded, size-capped binary read
 *  (base64 over the transport) — real pixels/playback, not placeholders. */
const MEDIA = /\.(svg|png|jpe?g|gif|webp|mp4|webm|wav|mp3|glb)$/i;
const KINDS = { image: /\.(svg|png|jpe?g|gif|webp)$/i, video: /\.(mp4|webm)$/i, audio: /\.(wav|mp3)$/i, "3d": /\.glb$/i, all: /.*/ } as const;
const TABS: Record<keyof typeof KINDS, I18nKey> = { image: "nep.tab-image", video: "nep.tab-video", audio: "nep.tab-audio", "3d": "nep.tab-3d", all: "nep.tab-all" };

/** SVG goes through the redacted text read — its bytes can carry
 *  sensitive content that the raw binary path must never expose. */
function svgDataUrl(text: string): string {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(text)}`;
}

function PreviewBody({ preview, name }: { preview: ArtifactPreview; name: string }) {
  if (preview.result !== "ok") {
    const key: I18nKey = preview.result === "missing" ? "art.missing"
      : preview.result === "too_large" ? "art.too-large"
      : preview.result === "unsupported" ? "art.unsupported"
      : "art.preview-failed";
    return <p className="omni-empty">{key === "art.preview-failed" ? t(key, { msg: preview.result }) : t(key)}</p>;
  }
  const src = `data:${preview.mime};base64,${preview.dataB64}`;
  if (preview.mime.startsWith("image/")) return <img alt={name} src={src} />;
  if (preview.mime.startsWith("video/")) return <video controls src={src} />;
  if (preview.mime.startsWith("audio/")) return <audio controls src={src} />;
  return <p className="omni-empty">{t("art.unsupported")}</p>;
}

export default function NeptuneWorkspace({ ctx }: { ctx: RunCtx }) {
  const { runs, service } = ctx;
    const scene = SCENES[8];
  const [tab, setTab] = useState<keyof typeof KINDS>("image");
  const [sel, setSel] = useState<string | null>(null);
  const [op, setOp] = useState<{ ok: boolean; text: string } | null>(null);

  // 本台产物为默认范围 —— 其他工作台的媒体只有明确勾选才进入列表。
  const [allRuns, setAllRuns] = useState(false);
  const media = useMemo(() => {
    const scope = allRuns ? runs : workspaceRuns(ctx, "neptune");
    return scope.flatMap((r) => r.artifacts.filter((a) => MEDIA.test(a.path)).map((a) => ({ run: r, artifact: a })));
  }, [runs, allRuns, ctx]);
  const visible = media.filter((m) => KINDS[tab].test(m.artifact.path));
  const cur = visible.find((m) => m.artifact.artifactId === sel) ?? visible[0];
  const isSvg = cur?.artifact.path.endsWith(".svg") ?? false;
  const [content, setContent] = useState<
    | { kind: "svg"; text: string; stale: boolean; result: string }
    | { kind: "bin"; preview: ArtifactPreview }
    | null
  >(null);
  const [readErr, setReadErr] = useState<string | null>(null);
  useEffect(() => {
    if (!cur) { setContent(null); return; }
    let staleFlag = false;
    setContent(null); setReadErr(null);
    const req = {
      tenantId: service.identity.tenantId,
      sessionId: cur.run.sessionId,
      runId: cur.run.runId,
      artifactId: cur.artifact.artifactId,
    };
    const apply = (fn: () => void) => { if (!staleFlag) fn(); };
    const fail = (e: unknown) => apply(() => setReadErr(String(e instanceof Error ? e.message : e)));
    if (isSvg) {
      void service.readArtifact(req)
        .then((c) => apply(() => setContent({ kind: "svg", text: c.text, stale: c.stale, result: c.result })))
        .catch(fail);
    } else {
      void service.readArtifactPreview(req)
        .then((p) => apply(() => setContent({ kind: "bin", preview: p })))
        .catch(fail);
    }
    return () => { staleFlag = true; };
  }, [service, cur?.artifact.artifactId, cur?.run.runId, cur?.run.sessionId, isSvg]);

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
              <div className="media-svg">
                {(content?.kind === "svg" ? content.stale : content?.preview.stale) && <p className="omni-empty">{t("art.stale")}</p>}
                {readErr && <p className="omni-empty">{readErr}</p>}
                {content?.kind === "svg" && (
                  content.result !== "ok"
                    ? <p className="omni-empty">{content.result === "missing" ? t("art.missing") : content.result === "unsupported" ? t("art.unsupported") : t("art.preview-failed", { msg: content.text })}</p>
                    : <img alt={cur.artifact.path.split("/").pop()} src={svgDataUrl(content.text)} />
                )}
                {content?.kind === "bin" && <PreviewBody name={cur.artifact.path.split("/").pop() ?? ""} preview={content.preview} />}
                {!content && !readErr && <p className="omni-empty">…</p>}
              </div>
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
          <h4 className="ws-mod">{t("nep.works")}
            <button className="ven-link" onClick={() => setAllRuns((v) => !v)} title={t("nep.scope-hint")} type="button">
              {allRuns ? t("nep.scope-all") : t("nep.scope-own")}
            </button>
          </h4>
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
                // 委派携带源产物引用 — 子任务拿到的是已验证的 artifact:
                // ref（嵌在 handoff + 请求文本里），不是路径复述。
                void delegateToPlanet(ctx, cur.run, "neptune", mediaDelegationRequest(cur.artifact.path, cur.run.runId), [`artifact:${cur.artifact.artifactId}`])
                  .then(() => setOp({ ok: true, text: t("nep.delegated") }))
                  .catch((e) => setOp({ ok: false, text: String(e instanceof Error ? e.message : e) }));
              }} type="button">{t("nep.render-mp4")}</button>
              {op && <p className="omni-empty">{op.text}</p>}
            </>
          )}
          <CapNotInstalled what={t("nep.cap-gen")} />
                    <WorkspaceTasks ctx={ctx} planet="neptune" />
          <div className="ws-statusbar"><span>{visible.length} ITEMS</span><span>{t(TABS[tab])}</span><span className="right">neptune</span></div>
        </aside>
      </div>
    </WorkspaceFrame>
  );
}
