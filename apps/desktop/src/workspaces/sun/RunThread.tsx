import { useEffect, useState } from "react";
import { ThinkingChain } from "./ThinkingChain";
import { StatusBadge, MarkdownText, rel, statusLabel, stageLabel } from "../../app/ui";
import { t } from "../../app/i18n";
import { draftKey } from "../../app/draftStore";
import { runAttachments } from "../../app/runAttachments";
import { useScrollFollow } from "../../app/useScrollFollow";
import type { RunCtx } from "../../app/runCtx";
import type { RunView, StageStatus } from "../../types";
import type { ApprovalSummary } from "../../types/service";
import OmniLanding from "./OmniLanding";

/** task duration — createdAt → durable terminal mark when one exists,
 *  else latest event timestamp, else now. Event-evidenced only. */
const dur = (r: { createdAt?: string; output?: { createdAt?: string }; events?: { timestamp?: string }[] }) => {
  const start = Date.parse(r.createdAt ?? "");
  if (Number.isNaN(start)) return "";
  const end = Date.parse(r.output?.createdAt ?? "") || Date.parse(r.events?.slice(-1)[0]?.timestamp ?? "") || Date.now();
  const s = Math.max(0, Math.round((end - start) / 1000));
  return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m${String(s % 60).padStart(2, "0")}s` : `${Math.floor(s / 3600)}h${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
};

export interface WfStep { stage: string; wf: string; status: StageStatus; duration?: string }

interface Props {
  ctx: RunCtx;
  viewRun: RunView | undefined;
  approvals: ApprovalSummary[];
  wfSteps: WfStep[];
  curPhase: string;
}

/** Chat column — task head + every run of the session rendered
 *  chronologically (request bubble, thinking chain, streamed/durable
 *  reply), live workflow steps, artifact rows, failure notice, scroll pill. */
export default function RunThread({ ctx, viewRun, approvals, wfSteps, curPhase }: Props) {
  const { service, sessionId, runs } = ctx;
  const [artPreview, setArtPreview] = useState<{ id: string; text: string; stale: boolean; result: string } | null>(null);
  const isBlocked = viewRun?.status === "blocked";
  // thread scroll-follow keyed by the viewed run — restores its position
  const thread = useScrollFollow(viewRun?.runId ?? "landing");
  useEffect(() => { thread.onContentGrow(); });

  return (
    <div className="omni-thread3" onScroll={thread.onScroll} ref={thread.ref}>
      {/* task head — status / phase / duration / pending action, all
          sourced from real run + event fields */}
      {viewRun && (
        <div className="task-head3">
          <StatusBadge status={viewRun.status} />
          <code className="task-run">{viewRun.runId.replace("run-", "#")}</code>
          {curPhase && <span className="task-phase">{curPhase}</span>}
          <em className="task-dur">{dur(viewRun)}</em>
          {isBlocked && approvals.length > 0 && <span className="task-awaiting">{t("task.awaiting", { n: approvals.length })}</span>}
        </div>
      )}
      {!viewRun && <OmniLanding draftKey={draftKey(service.identity.tenantId, ctx.sessionId, "sun")} />}
      {/* whole-session history — every run contributes its messages,
          chronological (hydrated runs arrive unordered) */}
      {runs.filter((r) => r.sessionId === sessionId)
        .sort((a, b) => (a.createdAt ?? "").localeCompare(b.createdAt ?? ""))
        .map((r) => {
        const conv = r.conversation ?? [];
        const isStream = (m: (typeof conv)[number]) => (m as { streaming?: boolean }).streaming === true;
        const thinkingMsgs = conv.filter((m) => m.role !== "user" && !isStream(m));
        const liveReply = conv.filter(isStream).slice(-1)[0];
        const showLive = liveReply !== undefined && !(r.output && r.output.text);
        return (
        <div className="sess-run" key={r.runId}>
          {r !== viewRun && <div className="sess-run-sep"><code>{r.runId.replace("run-", "#")}</code><em>{statusLabel(r.status)}</em></div>}
          {r.request && (
            <article className="msg3 user">
              <div className="msg3-head"><span>{t("chat.you")}</span><time>{rel(r.createdAt)}</time></div>
              <div className="msg3-body"><MarkdownText text={r.request} /></div>
              {runAttachments(r.runId).length > 0 && (
                <div className="attach-row">
                  {runAttachments(r.runId).map((f, i) => (
                    <span className="attach-chip" key={i} title={f}>📎 {f.split("/").pop()}</span>
                  ))}
                </div>
              )}
            </article>
          )}
          <ThinkingChain canResume={r === viewRun && ctx.canResume} events={r.events} messages={thinkingMsgs} onResume={r === viewRun ? ctx.resumeActiveRun : undefined} />
          {showLive && (
            <article className="msg3 assistant">
              <div className="msg3-head"><span>⬡ T.S.A</span><time></time></div>
              <div className="msg3-body"><MarkdownText text={liveReply.content} /><span className="msg-caret" /></div>
              <button className="msg-copy" onClick={() => void navigator.clipboard.writeText(liveReply.content)} title={t("common.copy")}>{t("common.copy")}</button>
            </article>
          )}
          {r.output && (
            <article className="msg3 assistant durable-output">
              <div className="msg3-head"><span>⬡ T.S.A</span><time>{rel(r.output.createdAt)}</time></div>
              <div className="msg3-body"><MarkdownText text={r.output.text} /></div>
            </article>
          )}
        </div>
        );
      })}
      {/* CURRENT EXECUTION — live workflow stages; terminal runs keep
          their step record inside the collapsed thinking box instead */}
      {wfSteps.length > 0 && viewRun && !["completed", "failed", "cancelled", "timed_out"].includes(viewRun.status) && (
        <article className="msg3 exec mech">
          <div className="msg3-head"><span className="mod-title">{t("sun.current-execution")}</span><em>{statusLabel(viewRun.status)}</em></div>
          <div className="exec-steps plan">
            {wfSteps.map((st, i) => (
              <div className={`plan-step ${st.status} ${st.status === "running" ? "current" : ""}`} key={i}>
                <span className="plan-num">{st.status === "completed" || st.status === "verified" ? "✓" : i + 1}</span>
                <div className="plan-body">
                  <strong>{st.stage}</strong>
                  <em>{st.wf} · {st.status === "interrupted" && isBlocked ? t("sun.waiting-approval") : stageLabel(st.status)}{st.duration ? ` · ${st.duration}` : ""}</em>
                </div>
              </div>
            ))}
          </div>
        </article>
      )}

      {/* TASK OUTPUTS — shown for completed/failed/cancelled alike;
          preview rides the existing readArtifact contract; references
          stay opaque (artifactId copied, paths never concatenated) */}
      {(viewRun?.artifacts ?? []).length > 0 && (
        <article className="msg3 artifacts mech">
          <div className="msg3-head"><span className="mod-title">{t("sun.task-outputs")}</span><em></em></div>
          <div className="artifact-rows">
            {viewRun!.artifacts.map((a) => (
              <div className="artifact-item" key={a.artifactId}>
                <div className={`artifact-row ${a.status}`} title={a.path}>
                  <strong>📄 {a.path.split("/").pop() ?? a.artifactId}</strong>
                  <small>{a.path.split(".").pop()?.toUpperCase()}</small>
                  <em className={a.status === "generated" ? "" : "pending"}>{a.status}</em>
                  <span className="artifact-acts">
                    <button className="artifact-act" onClick={() => {
                      if (artPreview?.id === a.artifactId) { setArtPreview(null); return; }
                      void service.readArtifact({
                        sessionId: viewRun!.sessionId,
                        runId: viewRun!.runId, artifactId: a.artifactId,
                      }).then((c) => setArtPreview({ id: a.artifactId, text: c.text, stale: c.stale, result: c.result }))
                        .catch((e) => setArtPreview({ id: a.artifactId, text: String(e instanceof Error ? e.message : e), stale: false, result: "error" }));
                    }} type="button">{t("artifact.preview")}</button>
                    <button className="artifact-act" onClick={() => void navigator.clipboard.writeText(a.artifactId)} type="button">{t("artifact.copy-ref")}</button>
                    {a.path && <button className="artifact-act" onClick={() => void service.revealPath(a.path)} type="button">{t("artifact.reveal")}</button>}
                  </span>
                </div>
                {artPreview?.id === a.artifactId && (
                  <div className="artifact-preview">
                    {artPreview.result !== "ok" && <p className="omni-empty">{artPreview.result}: {artPreview.text.slice(0, 200)}</p>}
                    {artPreview.stale && <p className="omni-empty">file changed since recorded</p>}
                    <pre className="artifact-preview-body">{artPreview.text.slice(0, 4000)}</pre>
                  </div>
                )}
              </div>
            ))}
          </div>
        </article>
      )}
      {viewRun?.failure && !isBlocked && <article className="msg3 error"><div className="msg3-body">{viewRun.failure.message}</div></article>}
      {thread.hasNew && (
        <button className="scroll-pill" onClick={thread.toBottom} type="button">{t("thread.new-content")} ↓</button>
      )}
    </div>
  );
}
