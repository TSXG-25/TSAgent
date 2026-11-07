import { useMemo, useState } from "react";
import { SCENES } from "../../app/scenes";
import { WorkspaceFrame } from "../../design-system/WorkspaceFrame";
import AgentComposer from "../../app/AgentComposer";
import { delegateToPlanet } from "../../app/delegate";
import { CapNotInstalled } from "../../design-system/WorkspaceFrame";
import type { RunCtx } from "../../app/runCtx";

/** Media Studio per hand-drawn spec §10 — type tabs (图像/视频/3D/预览) |
 *  media canvas | right ops | composer. */
const MEDIA = /\.(svg|png|jpe?g|gif|mp4|webm|wav|mp3|glb)$/i;
const KINDS = { "图像": /\.(svg|png|jpe?g|gif)$/i, "视频": /\.(mp4|webm)$/i, "3D": /\.glb$/i, "预览": /.*/ } as const;

export default function NeptuneWorkspace({ ctx }: { ctx: RunCtx }) {
  const { runs } = ctx;
  const scene = SCENES[8];
  const [tab, setTab] = useState<keyof typeof KINDS>("图像");
  const [sel, setSel] = useState<string | null>(null);

  const media = useMemo(() => runs.flatMap((r) => r.artifacts.filter((a) => MEDIA.test(a.path)).map((a) => ({ run: r, artifact: a }))), [runs]);
  const visible = media.filter((m) => KINDS[tab].test(m.artifact.path));
  const cur = visible.find((m) => m.artifact.artifactId === sel) ?? visible[0];

  return (
    <WorkspaceFrame scene={scene} ctx={ctx}>
      <div className="media">
        <div className="media-main">
          <div className="seg-tabs media-tabs">
            {(Object.keys(KINDS) as (keyof typeof KINDS)[]).map((t) => (
              <button className={tab === t ? "on" : ""} key={t} onClick={() => { setTab(t); setSel(null); }} type="button">{t}</button>
            ))}
          </div>
          <div className="media-canvas">
            {cur ? (
              cur.artifact.path.endsWith(".svg")
                ? <div className="media-svg" dangerouslySetInnerHTML={{ __html: cur.artifact.content }} />
                : <div className="empty-state"><p>{cur.artifact.path.split("/").pop()}</p><p className="omni-empty">{cur.artifact.size} — 二进制预览待接入</p></div>
            ) : <div className="empty-state"><p>还没有 {tab} 产物</p></div>}
          </div>
          <AgentComposer ctx={ctx} planet="neptune" placeholder="生成指令 — @neptune 出图…" />
        </div>
        <aside className="media-side">
          <h4 className="ws-mod">工作</h4>
          {visible.map(({ artifact: a }) => (
            <button className={`nep-thumb ${cur?.artifact.artifactId === a.artifactId ? "on" : ""}`} key={a.artifactId} onClick={() => setSel(a.artifactId)} type="button">
              {a.path.endsWith(".svg") ? <span className="nep-thumb-svg" dangerouslySetInnerHTML={{ __html: a.content }} /> : <span className="nep-thumb-kind">{a.path.split(".").pop()?.toUpperCase()}</span>}
            </button>
          ))}
          <h4 className="ws-mod">偏好</h4>
          {cur && (
            <button className="btn secondary" onClick={() => void delegateToPlanet(ctx, "neptune", `将 ${cur.artifact.path} 渲染为 MP4 视频并保存到 output/`)} type="button">▶ 渲染 MP4</button>
          )}
          <CapNotInstalled what="生成/编辑能力" />
          <div className="ws-statusbar"><span className="sys-ok">● ONLINE</span><span>{visible.length} ITEMS</span><span>{tab}</span><span className="right">neptune</span></div>
        </aside>
      </div>
    </WorkspaceFrame>
  );
}
