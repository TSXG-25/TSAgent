import { useEffect } from "react";
import { t } from "./i18n";
import { MarkdownText, rel, statusLabel } from "./ui";
import type { RunCtx } from "./runCtx";
import type { RunView } from "../types";
import { workspaceRuns } from "./workspaceSelection";
import { ThinkingChain } from "../workspaces/sun/ThinkingChain";

const LIVE = ["active", "blocked", "pending"];

/** One run's exchange — request bubble, thinking record, streamed reply and
 *  durable output. Same message contract as Omni's thread; a workstation's
 *  conversation record is read in place, never via the Omni surface. */
function RunExchange({ run }: { run: RunView }) {
  const conv = run.conversation ?? [];
  const isStream = (m: (typeof conv)[number]) => (m as { streaming?: boolean }).streaming === true;
  const thinkingMsgs = conv.filter((m) => m.role !== "user" && !isStream(m));
  const liveReply = conv.filter(isStream).slice(-1)[0];
  const showLive = liveReply !== undefined && !(run.output && run.output.text);
  return (
    <div className="sess-run">
      <div className="sess-run-sep"><code>{run.runId.replace("run-", "#")}</code><em>{statusLabel(run.status)}</em></div>
      {run.request && (
        <article className="msg3 user">
          <div className="msg3-head"><span>{t("chat.you")}</span><time>{rel(run.createdAt)}</time></div>
          <div className="msg3-body"><MarkdownText text={run.request} /></div>
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
  const liveIds = mine.filter((r) => LIVE.includes(r.status)).map((r) => r.runId).join(",");

  // Catalog slices arrive without messages — hydrate each run once, then
  // poll only while non-terminal so streamed replies land in place.
  useEffect(() => {
    for (const id of runIds.split(",")) {
      if (id) void ctx.controller.refreshRun(id, sessionId).catch(() => {});
    }
  }, [ctx.controller, sessionId, runIds]);
  useEffect(() => {
    if (!liveIds) return;
    const tick = setInterval(() => {
      for (const id of liveIds.split(",")) void ctx.controller.refreshRun(id, sessionId).catch(() => {});
    }, 4000);
    return () => clearInterval(tick);
  }, [ctx.controller, sessionId, liveIds]);

  return (
    <div className="ws-thread">
      {mine.length === 0 && <p className="omni-empty">{t("wstasks.empty")}</p>}
      {mine.map((run) => <RunExchange key={run.runId} run={run} />)}
    </div>
  );
}
