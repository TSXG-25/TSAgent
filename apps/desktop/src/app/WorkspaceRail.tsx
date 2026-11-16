import { useEffect, useState } from "react";
import { t } from "./i18n";
import { Icon, StatusBadge, rel, statusLabel } from "./ui";
import type { RunCtx } from "./runCtx";
import type { SessionSummary } from "../types/service";
import { PLANET_GLYPH } from "./scenes";
import { sendNewRecord } from "./delegate";
import AgentComposer from "./AgentComposer";
import { WorkspaceThread } from "./WorkspaceThread";
import { useWorkspaceCurrentRun } from "./workspaceSelection";

/** Open a record's thread — unconditional: re-picking the session the
 *  rail already shows must still leave home, so the view transition
 *  cannot ride the sessionId-change effect alone. */
export function openRecord(
  ctx: Pick<RunCtx, "selectSession">,
  setView: (view: "home" | "thread") => void,
  sessionId: string,
): void {
  ctx.selectSession(sessionId);
  setView("thread");
}

/** Workstation rail — two states mirroring the Cline pattern.
 *  home: entry hero + RECENT sessions + composer (a send files a fresh
 *        session as a new record, Cline's "new task").
 *  thread: the selected session's own transcript + composer; "+" returns
 *        home. Records stay on the workstation — nothing here reads or
 *        writes another planet's view. */
export function WorkspaceRail({ ctx, planet, placeholder }: { ctx: RunCtx; planet: string; placeholder?: string }) {
  // The session's record is the default surface — home is an explicit
  // destination reached through "+", never a transient state.
  const [view, setView] = useState<"home" | "thread">("thread");
  const currentRun = useWorkspaceCurrentRun(ctx, planet);
  const live = currentRun && ["active", "blocked", "pending"].includes(currentRun.status);

  // A session switch always lands on its record; re-opening home bumps
  // the epoch so the recent list refetches.
  const [homeEpoch, setHomeEpoch] = useState(0);
  useEffect(() => { setView("thread"); }, [ctx.sessionId]);

  const openHome = () => { setHomeEpoch((e) => e + 1); setView("home"); };

  return (
    <div className={`rail rail-${view}`}>
      {view === "home" ? (
        <RailHome ctx={ctx} planet={planet} epoch={homeEpoch} onPick={(id) => openRecord(ctx, setView, id)} />
      ) : (
        <>
          <div className="rail-head">
            <button type="button" className="rail-home-btn" title={t("wsrail.home")} onClick={openHome}>
              <Icon name="plus" size={14} />
            </button>
            <div className="rail-head-title">
              <strong>{ctx.sessionId.slice(-8)}</strong>
              {currentRun && (
                <em><StatusBadge status={currentRun.status} /> {statusLabel(currentRun.status)}</em>
              )}
            </div>
            {live && (
              <span className="rail-head-ops">
                <button type="button" className="btn secondary ws-task-op" onClick={() => void ctx.controller.pauseRun(currentRun!.runId)}>{t("wstasks.pause")}</button>
                <button type="button" className="btn secondary ws-task-op" onClick={() => void ctx.controller.cancelRun(currentRun!.runId)}>{t("wstasks.cancel")}</button>
              </span>
            )}
          </div>
          <WorkspaceThread ctx={ctx} planet={planet} />
        </>
      )}
      <AgentComposer
        ctx={ctx}
        planet={planet}
        placeholder={placeholder}
        onSend={view === "home"
          ? (to, body, files, options) => sendNewRecord(ctx, planet, to, body, files, options)
          : undefined}
      />
    </div>
  );
}

/** Home state — hero plus the planet's own session list (durable planet
 *  claim; unclaimed sessions stay Omni's). Row title = latest run's
 *  request from the planet tenant catalog, never guessed metadata. */
function RailHome({ ctx, planet, epoch, onPick }: { ctx: RunCtx; planet: string; epoch: number; onPick: (sessionId: string) => void }) {
  const service = ctx.service;
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  useEffect(() => {
    let alive = true;
    void ctx.controller.loadTenantCatalog(planet);
    void service.listSessions({
      tenantId: service.identity.tenantId,
      userId: service.identity.userId,
      requestId: `rail-list-${planet}-${Date.now()}`,
    }).then((list) => { if (alive) setSessions(list); }).catch(() => {});
    return () => { alive = false; };
  }, [service, planet, epoch, ctx.controller]);

  const rows = sessions
    .filter((s) => s.planet === planet)
    .sort((a, b) => b.latestUpdatedAt.localeCompare(a.latestUpdatedAt));
  const titleOf = (s: SessionSummary) =>
    ctx.runs.find((r) => r.runId === s.latestRunId)?.request?.slice(0, 60) ?? s.sessionId;

  return (
    <div className="rail-home">
      <div className="rail-hero">
        <span className="rail-hero-glyph">{PLANET_GLYPH[planet]}</span>
        <h3>{t("wsrail.hero")}</h3>
      </div>
      <div className="rail-recent-head"><span>{t("wsrail.recent")}</span></div>
      <div className="rail-recent">
        {rows.length === 0 && <p className="omni-empty">{t("wsrail.empty")}</p>}
        {rows.map((s) => (
          <button type="button" className="rail-recent-row" key={s.sessionId} onClick={() => onPick(s.sessionId)}>
            <span className="rail-recent-title">{titleOf(s)}</span>
            <span className="rail-recent-meta">
              {s.latestStatus && <em className={`sess-st ${s.latestStatus}`}>{statusLabel(s.latestStatus)}</em>}
              <time>{rel(s.latestUpdatedAt)}</time>
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
