import { useCallback, useEffect, useMemo, useState } from "react";
import { SCENES } from "../../app/scenes";
import { WorkspaceFrame } from "../../design-system/WorkspaceFrame";
import AgentComposer from "../../app/AgentComposer";
import { requestIdForRun, StatusBadge, Icon, MarkdownText } from "../../app/ui";
import { useOrchestration } from "./SunOrchestration";
import type { RunCtx } from "../../app/runCtx";
import type { ApprovalSummary, ParticipantSummary, SessionSummary } from "../../types/service";

/** Omni v3 — task-workbench layout per review:
 *  SessionSidebar (task titles+status+search) | TaskControl bar +
 *  constrained chat column (normal / execution / artifact messages) +
 *  AgentComposer | collapsible context slot | status footer.
 *  All data real: sessions.latestStatus, run request/status/events/
 *  artifacts/approvals. Nothing fabricated. */

const rel = (iso: string) => {
  const d = Date.now() - Date.parse(iso);
  if (!iso || Number.isNaN(d)) return "";
  const m = Math.floor(d / 60000);
  return m < 1 ? "刚刚" : m < 60 ? `${m}分钟前` : m < 1440 ? `${Math.floor(m / 60)}小时前` : `${Math.floor(m / 1440)}天前`;
};

export default function SunWorkspace({ ctx }: { ctx: RunCtx }) {
  const { service } = ctx;
  const scene = SCENES[0];
  const orch = useOrchestration(ctx);
  const { activeRun, runs } = ctx;
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionId, setSessionId] = useState(service.identity.sessionId);
  const [query, setQuery] = useState("");
  const [approvals, setApprovals] = useState<ApprovalSummary[]>([]);
  const [participants, setParticipants] = useState<ParticipantSummary[]>([]);
  const [folders, setFolders] = useState<Record<string, string>>(() => {
    try { return JSON.parse(localStorage.getItem("tsagent:folders") ?? "{}") as Record<string, string>; } catch { return {}; }
  });
  const folderOf = (sid: string) => folders[sid] ?? "";
  const setFolder = (sid: string, name: string) => {
    const next = { ...folders };
    if (name.trim()) next[sid] = name.trim(); else delete next[sid];
    setFolders(next);
    localStorage.setItem("tsagent:folders", JSON.stringify(next));
  };
  const folderNames = [...new Set(Object.values(folders))].sort();
  const byFolder = (name: string) => filtered.filter((s) => folderOf(s.sessionId) === name);
  const [showReason, setShowReason] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [ss, ap, pt] = await Promise.all([
        service.listSessions({ tenantId: service.identity.tenantId, userId: service.identity.userId, requestId: requestIdForRun(0) } as never),
        service.listApprovals({ tenantId: service.identity.tenantId, sessionId: service.identity.sessionId, requestId: requestIdForRun(0), status: "PENDING" } as never),
        service.listParticipants({ tenantId: service.identity.tenantId, sessionId, requestId: requestIdForRun(0) } as never).catch(() => [] as ParticipantSummary[]),
      ]);
      setSessions(ss); setApprovals(ap); setParticipants(pt);
      const sel = ss.find((x) => x.sessionId === sessionId);
      if (sel?.latestRunId && !runs.find((r) => r.runId === sel.latestRunId)) {
        void ctx.controller.refreshRun(sel.latestRunId, sessionId);
      }
    } catch { /* non-fatal */ }
  }, [service]);
  useEffect(() => { void refresh(); const t = setInterval(() => void refresh(), 8000); return () => clearInterval(t); }, [refresh]);
  useEffect(() => {
    const onNew = () => void newSession();
    window.addEventListener("tsagent:new-session", onNew);
    return () => window.removeEventListener("tsagent:new-session", onNew);
  });

  const newSession = async () => {
    const s = await service.createSession({
      tenantId: service.identity.tenantId, userId: service.identity.userId,
      sessionId: `session-${Date.now().toString(36)}`, requestId: `sun-new-${Date.now()}`,
    } as never);
    await refresh(); setSessionId(s.sessionId); service.setSession(s.sessionId);
  };

  // session → latest run request as display title (real data)
  const titleOf = useCallback((sid: string, latestRunId?: string) => {
    const r = runs.find((x) => x.sessionId === sid && (!latestRunId || x.runId === latestRunId)) ?? runs.find((x) => x.sessionId === sid);
    return r?.request?.slice(0, 28) ?? sid;
  }, [runs]);

  const viewRun = useMemo(() => {
    const s = sessions.find((x) => x.sessionId === sessionId);
    return runs.find((r) => r.runId === s?.latestRunId) ?? runs.find((r) => r.sessionId === sessionId) ?? ctx.activeRun;
  }, [sessionId, sessions, runs, ctx.activeRun]);
  const wfSteps = useMemo(() => (viewRun?.workflows ?? []).flatMap((w) => w.stages.map((st) => ({ stage: st.name, wf: w.name, status: st.status, duration: st.duration }))), [viewRun]);
  const activeCount = runs.filter((r) => r.status === "active").length;
  const isBlocked = viewRun?.status === "blocked";
  const filtered = sessions.filter((s) => !query || titleOf(s.sessionId, s.latestRunId).includes(query) || s.sessionId.includes(query));

  return (
    <WorkspaceFrame scene={scene} ctx={ctx}>
      <div className="omni3">
        {/* ── Session sidebar ── */}
        <aside className="omni-rail">
          <div className="omni-rail-top">
            <span className="omni-eyebrow">◈ WORKSPACE</span>
            <button className="omni-new" onClick={() => void newSession()} type="button">＋ 新建任务</button>
            <div className="omni-search"><Icon name="search" size={12} /><input onChange={(e) => setQuery(e.target.value)} placeholder="搜索会话" value={query} /></div>
          </div>
          <div className="omni-sess-scroll">
            {(() => {
              const allSids = [service.identity.sessionId, ...filtered.map((s) => s.sessionId)].filter((v, i, a) => a.indexOf(v) === i);
              const row = (sid: string) => {
                const s = sessions.find((x) => x.sessionId === sid);
                const st = s?.latestStatus?.toUpperCase() ?? "";
                return (
                  <button className={`omni-sess3 ${sid === sessionId ? "on" : ""}`} key={sid} onClick={() => {
                    setSessionId(sid);
                    service.setSession(sid);
                    const rid = s?.latestRunId;
                    if (rid && !runs.find((r) => r.runId === rid)) void ctx.controller.refreshRun(rid, sid);
                  }} onContextMenu={(e) => { e.preventDefault(); const n = window.prompt("文件夹（留空移出）", folderOf(sid)); if (n !== null) setFolder(sid, n); }} type="button">
                    <span className={`omni-sess-dot ${st === "BLOCKED" || st === "FAILED" ? "blocked" : st === "ACTIVE" ? "active" : ""}`} />
                    <span className="omni-sess-info">
                      <strong>{titleOf(sid, s?.latestRunId)}</strong>
                      <small>{st && <em className={`sess-st ${st.toLowerCase()}`}>{st}</em>} {rel(s?.latestUpdatedAt ?? "")}</small>
                    </span>
                  </button>
                );
              };
              const unfiled = allSids.filter((sid) => !folderOf(sid));
              return (
                <>
                  {folderNames.map((name) => (
                    <div className="omni-folder" key={name}>
                      <span className="omni-group-label">📁 {name}</span>
                      {allSids.filter((sid) => folderOf(sid) === name).map(row)}
                    </div>
                  ))}
                  <span className="omni-group-label">最近</span>
                  {unfiled.map(row)}
                </>
              );
            })()}
            {filtered.length === 0 && query && <p className="omni-empty">无匹配会话</p>}
          </div>
        </aside>

        {/* ── Main column ── */}
        <main className="omni-col">
          {/* pending approval — only chrome that still earns its place */}
          {isBlocked && approvals.length > 0 && (
            <section className="omni-taskbar blocked">
              <span className="omni-warn">⚠ {approvals[0].action} — {String(approvals[0].params?.path ?? approvals[0].scope)}</span>
              <button className="btn primary" onClick={() => void service.decideApproval({ tenantId: service.identity.tenantId, sessionId: service.identity.sessionId, requestId: requestIdForRun(0), approvalId: approvals[0].approvalId, decision: "APPROVED" } as never).then(refresh)} type="button">批准</button>
              <button className="btn danger" onClick={() => void service.decideApproval({ tenantId: service.identity.tenantId, sessionId: service.identity.sessionId, requestId: requestIdForRun(0), approvalId: approvals[0].approvalId, decision: "DENIED" } as never).then(refresh)} type="button">拒绝</button>
            </section>
          )}

          {/* Chat column — request bubble + streamed/final reply */}
          <div className="omni-thread3">
            {!viewRun && <div className="empty-state"><p>选择或新建任务开始</p></div>}
            {viewRun?.request && (
              <article className="msg3 user" key="req">
                <div className="msg3-head"><span>You</span><time>{rel(viewRun.createdAt)}</time></div>
                <div className="msg3-body"><MarkdownText text={viewRun.request} /></div>
              </article>
            )}
            {viewRun && (viewRun.conversation ?? []).map((m) => (
              <article className={`msg3 ${m.role}`} key={m.messageId}>
                <div className="msg3-head"><span>{m.role === "user" ? "You" : "⬡ T.S.A"}</span><time>{rel(m.at)}</time></div>
                <div className="msg3-body"><MarkdownText text={m.content} />{viewRun.status === "active" && m.role !== "user" && <span className="msg-caret" />}</div>
              </article>
            ))}
            {viewRun?.output && (
              <article className="msg3 assistant durable-output" key="out">
                <div className="msg3-head"><span>⬡ T.S.A</span><time>{rel(viewRun.output.createdAt)}</time></div>
                <div className="msg3-body"><MarkdownText text={viewRun.output.text} /></div>
              </article>
            )}
            {/* CURRENT EXECUTION — real workflow stages from runtime */}
            {wfSteps.length > 0 && (
              <article className="msg3 exec mech">
                <div className="msg3-head"><span className="mod-title">⬡ CURRENT EXECUTION</span><em>{viewRun!.status.toUpperCase()}</em></div>
                <div className="exec-steps">
                  {wfSteps.map((t, i) => (
                    <div className={`exec-step ${t.status}`} key={i}>
                      <span className="exec-ic">{t.status === "completed" || t.status === "verified" ? "✓" : t.status === "failed" || (t.status === "interrupted" && !isBlocked) ? "✗" : t.status === "running" ? "◐" : "◌"}</span>
                      <span>{t.stage}</span>
                      <em>{t.status === "interrupted" && isBlocked ? "waiting approval" : t.status}{t.duration ? ` · ${t.duration}` : ""}</em>
                    </div>
                  ))}
                </div>
              </article>
            )}

            {/* RELATED RUNS — collapsed session history, not current steps */}
            {runs.filter((r) => r.runId !== viewRun?.runId).length > 0 && (
              <details className="msg3 related">
                <summary><span>▾ RELATED RUNS</span><em>{runs.filter((r) => r.runId !== viewRun?.runId).length}</em></summary>
                <div className="related-rows">
                  {runs.filter((r) => r.runId !== viewRun?.runId).map((r) => (
                    <div className="related-row" key={r.runId}>
                      <code>{r.runId.replace("run-", "#")}</code>
                      <span>{r.request?.slice(0, 40)}</span>
                      <em>{r.status}</em>
                    </div>
                  ))}
                </div>
              </details>
            )}
            {/* TASK OUTPUTS — generated vs pending, hover shows preview affordance */}
            {(viewRun?.artifacts ?? []).length > 0 && (
              <article className="msg3 artifacts mech">
                <div className="msg3-head"><span className="mod-title">◈ TASK OUTPUTS</span><em></em></div>
                <div className="artifact-rows">
                  {viewRun!.artifacts.map((a) => (
                    <button className={`artifact-row ${a.status}`} key={a.artifactId} title={a.path} type="button">
                      <strong>📄 {a.path.split("/").pop()}</strong>
                      <small>{a.path.split(".").pop()?.toUpperCase()}</small>
                      <em className={a.status === "generated" ? "" : "pending"}>{a.status}</em>
                      <span className="artifact-acts">预览 · Forge</span>
                    </button>
                  ))}
                </div>
              </article>
            )}
            {viewRun?.failure && !isBlocked && <article className="msg3 error"><div className="msg3-body">{viewRun.failure.message}</div></article>}
          </div>

          <AgentComposer ctx={ctx} planet="sun" placeholder="发送消息 / 创建任务，使用 @ 提及…" />

          {/* status footer — real counts */}
          <footer className="omni-statusbar">
            <span className="sys-ok">● SYSTEM ONLINE</span>
            <span>{activeCount} RUNNING · {runs.filter((r) => r.status === "blocked").length} WAITING</span>
            <span>{approvals.length} PENDING APPROVAL</span>
            <span className="omni-status-right">{participants.length > 0 ? `👥 ${participants.map((x) => `${x.role}:${x.participantId.slice(0,12)}`).join(" ")} · ` : ""}{sessionId}</span>
          </footer>
        </main>
      </div>
    </WorkspaceFrame>
  );
}
