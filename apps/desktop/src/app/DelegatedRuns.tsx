import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { StatusBadge } from "./ui";
import { usePoll } from "./usePoll";
import { selectWorkspaceRun } from "./workspaceSelection";
import { t } from "./i18n";
import type { RunCtx } from "./runCtx";
import type { RunView } from "../types";
import type { HandoffSummary } from "../types/service";

export type DelegatedRun = { handoff: HandoffSummary; run: RunView };

/** Durable delegation edges sourced from this session, resolved to the
 *  child runs that live in it — the shared "delegated out" surface behind
 *  Omni's thread cards and every workstation's task rail.
 *  Request-sequence guard (same as searchSession): only the newest request
 *  may write — a stale response can never overwrite fresher cards, whether
 *  it straddles a session switch (A→B→A) or an overlapping poll tick; a
 *  stale failure can't clear them either. Switching clears the snapshot. */
export function useDelegatedRuns(ctx: RunCtx): DelegatedRun[] {
  const { service, sessionId } = ctx;
  const [handoffs, setHandoffs] = useState<HandoffSummary[]>([]);
  const seq = useRef(0);
  usePoll(() => {
    const id = ++seq.current;
    void service.listHandoffs({ sessionId })
      .then((list) => { if (id === seq.current) setHandoffs(list); })
      .catch(() => { if (id === seq.current) setHandoffs([]); });
  }, 8_000, [service, sessionId]);
  useEffect(() => { setHandoffs([]); }, [sessionId]);
  return handoffs
    .map((handoff) => ({
      handoff,
      run: ctx.runs.find((r) => r.runId === handoff.targetRunId && r.sessionId === sessionId),
    }))
    .filter((x): x is DelegatedRun => Boolean(x.run));
}

/** "进入 X" — point the target workstation's session scope at the run's
 *  owning session (its rail filter and bound project follow the session),
 *  then select the run globally AND in the workstation's own selection
 *  record (status/pause/cancel read that record) before landing. */
export function openDelegatedRun(
  ctx: RunCtx,
  handoff: HandoffSummary,
  run: RunView,
  navigate: (path: string) => void,
): void {
  ctx.selectSessionFor(handoff.targetPlanet, run.sessionId);
  ctx.selectRun(run.runId);
  selectWorkspaceRun(ctx.service.identity.tenantId, run.sessionId, handoff.targetPlanet, run.runId);
  navigate(`/w/${handoff.targetPlanet}`);
}

/** Delegated-child cards — status, failure/result summary, inline select,
 *  and the enter-planet link that lands on the delegated record. */
export function DelegatedRunCards({ ctx, delegated }: { ctx: RunCtx; delegated: DelegatedRun[] }) {
  const navigate = useNavigate();
  if (!delegated.length) return null;
  return (
    <div className="chat-delegated" data-testid="chat-delegated">
      {delegated.map(({ handoff, run }) => (
        <article className="mc-delegated" key={handoff.handoffId}>
          <div className="mc-delegated-head">
            <span>@{handoff.targetPlanet || "?"}</span>
            <StatusBadge status={run.status} />
            <button
              className="ven-link"
              onClick={() => ctx.selectRun(run.runId)}
              type="button"
            >{run.request || run.runId}</button>
            <button
              className="ven-link"
              onClick={() => openDelegatedRun(ctx, handoff, run, navigate)}
              type="button"
            >{t("chat.enter-planet", { planet: handoff.targetPlanet })}</button>
          </div>
          {run.failure && <p className="mc-delegated-fail">{run.failure.message}</p>}
          {run.output && <p className="mc-delegated-out">{run.output.text.slice(0, 200)}</p>}
        </article>
      ))}
    </div>
  );
}
