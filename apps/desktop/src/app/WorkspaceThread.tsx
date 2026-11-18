import { useEffect } from "react";
import { usePoll } from "./usePoll";
import { t } from "./i18n";
import { splitIdeContext } from "./ideContext";
import { MarkdownText, rel, statusLabel } from "./ui";
import type { RunCtx } from "./runCtx";
import type { RunView } from "../types";
import { workspaceRuns } from "./workspaceSelection";
import { ThinkingChain } from "../workspaces/sun/ThinkingChain";


interface ChangeView {
  git?: boolean;
  added?: string[];
  modified?: string[];
  deleted?: string[];
  agent_files?: string[];
  ambiguous_files?: string[];
  user_dirty_preserved?: string[];
  diff?: Record<string, string>;
  diff_truncated?: boolean;
}

interface ObservationView {
  tool?: string;
  input?: Record<string, string>;
  result?: { ok?: boolean; error_code?: string; content?: string };
}

/** The terminal run event carries `payload.continuation` — workspace changes
 *  and action observations recorded by the launcher. */
function continuationOf(run: RunView): {
  workspace_changes?: ChangeView | null;
  observations?: ObservationView[];
} | null {
  for (const event of [...run.events].reverse()) {
    const c = (event.payload as { continuation?: Record<string, unknown> } | undefined)
      ?.continuation;
    if (c && typeof c === "object") return c as never;
  }
  return null;
}

function fileStatus(path: string, c: ChangeView): string {
  if (c.added?.includes(path)) return t("wsthread.f.added");
  if (c.deleted?.includes(path)) return t("wsthread.f.deleted");
  return t("wsthread.f.modified");
}

/** Task-level change preview + verification evidence for one run — the
 *  actual files touched, attribution vs pre-existing user edits, and the
 *  commands that ran with their real outcomes. */
function RunEvidence({ run }: { run: RunView }) {
  const cont = continuationOf(run);
  const changes = cont?.workspace_changes;
  const verifications = (cont?.observations ?? []).filter(
    (o) => o.tool === "shell" || o.tool === "run_python",
  );
  const changed = [
    ...(changes?.agent_files ?? []).map((path) => ({ path, ambiguous: false })),
    ...(changes?.ambiguous_files ?? []).map((path) => ({ path, ambiguous: true })),
  ];
  if (changed.length === 0 && verifications.length === 0) return null;
  return (
    <section className="run-evidence">
      {changed.length > 0 && changes && (
        <details className="ev-block" open>
          <summary>{t("wsthread.changes")} · {changed.length}</summary>
          <ul className="ev-files">
            {changed.map(({ path, ambiguous }) => (
              <li key={path}>
                <em className={`ev-tag ${ambiguous ? "warn" : ""}`}>
                  {fileStatus(path, changes)}
                </em>
                <code className="ev-path" title={path}>{path}</code>
                {ambiguous && (
                  <em className="ev-tag warn" title={t("wsthread.ambiguous-tip")}>
                    {t("wsthread.ambiguous")}
                  </em>
                )}
              </li>
            ))}
          </ul>
          {(changes.user_dirty_preserved?.length ?? 0) > 0 && (
            <p className="ev-note">
              {t("wsthread.preserved")} · {changes.user_dirty_preserved!.length}
            </p>
          )}
          {Object.entries(changes.diff ?? {}).map(([path, text]) => (
            <details className="ev-diff" key={path}>
              <summary><code>{path}</code></summary>
              <pre>{text}</pre>
            </details>
          ))}
          {changes.diff_truncated && <p className="ev-note">{t("wsthread.diff-truncated")}</p>}
        </details>
      )}
      {verifications.length > 0 && (
        <details className="ev-block">
          <summary>{t("wsthread.verify")} · {verifications.length}</summary>
          <ul className="ev-runs">
            {verifications.map((o, i) => (
              <li key={i}>
                <em className={`ev-tag ${o.result?.ok ? "ok" : "fail"}`}>
                  {o.result?.ok ? t("wsthread.pass") : t("wsthread.fail")}
                </em>
                <code className="ev-path">{o.input?.cmd ?? o.tool}</code>
                {!!o.result?.content && !o.result?.ok && (
                  <pre className="ev-out">{o.result.content.slice(-1200)}</pre>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

/** One run's exchange — request bubble, thinking record, streamed reply and
 *  durable output. Same message contract as Omni's thread; a workstation's
 *  conversation record is read in place, never via the Omni surface. */
function RunExchange({ run }: { run: RunView }) {
  const conv = run.conversation ?? [];
  const isStream = (m: (typeof conv)[number]) => (m as { streaming?: boolean }).streaming === true;
  const thinkingMsgs = conv.filter((m) => m.role !== "user" && !isStream(m));
  const liveReply = conv.filter(isStream).slice(-1)[0];
  const showLive = liveReply !== undefined && !(run.output && run.output.text);
  const req = splitIdeContext(run.request ?? "");
  return (
    <div className="sess-run">
      <div className="sess-run-sep"><code>{run.runId.replace("run-", "#")}</code><em>{statusLabel(run.status)}</em></div>
      {req.text && (
        <article className="msg3 user">
          <div className="msg3-head">
            <span>{t("chat.you")}</span>
            {req.file && <em className="req-ctx" title={req.file}>IDE · {req.file.split("/").pop()}</em>}
            <time>{rel(run.createdAt)}</time>
          </div>
          <div className="msg3-body"><MarkdownText text={req.text} /></div>
        </article>
      )}
      <ThinkingChain events={run.events} messages={thinkingMsgs} />
      {showLive && (
        <article className="msg3 assistant">
          <div className="msg3-head"><span>⬡ T.S.A</span><time></time></div>
          <div className="msg3-body"><MarkdownText text={liveReply.content} /><span className="msg-caret" /></div>
        </article>
      )}
      {run.output && (
        <article className="msg3 assistant durable-output">
          <div className="msg3-head"><span>⬡ T.S.A</span><time>{rel(run.output.createdAt)}</time></div>
          <div className="msg3-body"><MarkdownText text={run.output.text} /></div>
        </article>
      )}
      <RunEvidence run={run} />
      {run.failure && <p className="ws-task-fail">{run.failure.message}</p>}
    </div>
  );
}

/** Per-workstation transcript — this session's runs owned by `planet`
 *  (durable origin_planet claim, same membership as the task rail).
 *  Hydrates each run's conversation/events and live-polls while any run
 *  is non-terminal, so the reply lands in the workstation's own rail. */
export function WorkspaceThread({ ctx, planet }: { ctx: RunCtx; planet: string }) {
  const sessionId = ctx.sessionId;
  const mine = workspaceRuns(ctx, planet)
    .slice()
    .sort((a, b) => (a.createdAt ?? "").localeCompare(b.createdAt ?? ""));
  const runIds = mine.map((r) => r.runId).join(",");
  // Active/pending runs are long-polled by the controller; only runs parked
  // on an approval need polling here to notice they moved on.
  const blockedIds = mine.filter((r) => r.status === "blocked").map((r) => r.runId).join(",");

  // Catalog slices arrive without messages — hydrate each run once.
  // Refresh errors surface through the controller's error banner.
  useEffect(() => {
    for (const id of runIds.split(",")) {
      if (id) void ctx.controller.refreshRun(id, sessionId).catch(() => {});
    }
  }, [ctx.controller, sessionId, runIds]);
  usePoll(() => {
    for (const id of blockedIds.split(",")) void ctx.controller.refreshRun(id, sessionId).catch(() => {});
  }, blockedIds ? 4000 : null, [ctx.controller, sessionId, blockedIds]);

  return (
    <div className="ws-thread">
      {mine.length === 0 && <p className="omni-empty">{t("wstasks.empty")}</p>}
      {mine.map((run) => <RunExchange key={run.runId} run={run} />)}
    </div>
  );
}
