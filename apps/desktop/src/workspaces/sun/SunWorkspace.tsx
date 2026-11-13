import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { SCENES } from "../../app/scenes";
import { ThinkingChain } from "./ThinkingChain";
import { pickFolder, setSidecarPicker } from "../../service/tauriBridge";
import { WorkspaceFrame } from "../../design-system/WorkspaceFrame";
import AgentComposer from "../../app/AgentComposer";
import { requestIdForRun, StatusBadge, Icon, MarkdownText, statusLabel, stageLabel } from "../../app/ui";
import { t, tk } from "../../app/i18n";
import type { RunCtx } from "../../app/runCtx";
import type { ApprovalSummary, DirectoryListing, ParticipantSummary, SessionSummary, WorkspaceSummary } from "../../types/service";
import { fetchSessionSnapshot, RefreshGate } from "./sessionSnapshot";
import OmniLanding from "./OmniLanding";

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
  const { activeRun, runs, sessionId, selectSession } = ctx;
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [query, setQuery] = useState("");
  const [approvals, setApprovals] = useState<ApprovalSummary[]>([]);
  const [participants, setParticipants] = useState<ParticipantSummary[]>([]);
  const [wsList, setWsList] = useState<WorkspaceSummary[]>([]);
  const refreshWs = () => {
    void service.listWorkspaces({ tenantId: service.identity.tenantId, sessionId: service.identity.sessionId, requestId: `ws-${Date.now()}` }).then(setWsList).catch(() => {});
  };
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [dragSid, setDragSid] = useState<string | null>(null);
  const pickedSessionRef = useRef(false);
  // session → workspace path (backend resolves run scope from session_heads)
  const workspaceOf = (sid: string) => {
    const sess = sessions.find((x) => x.sessionId === sid);
    const ws = wsList.find((w) => w.workspaceId === (sess?.workspaceId ?? ""));
    return ws?.path ?? "";
  };
  const [browse, setBrowse] = useState<DirectoryListing | null>(null);
  useEffect(() => {
    // Only the local client can drive a real folder picker via the sidecar;
    // mock mode falls back to tauriBridge's webkitdirectory input.
    if (service.mode !== "local") return;
    setSidecarPicker(() => service.pickFolder());
    return () => setSidecarPicker(null);
  }, [service]);
  const [attachMap] = useState(() => new Map<string, string[]>()); // runId → sent file paths
  const [searchOpen, setSearchOpen] = useState(false);
  const openBrowse = (p = "") => {
    void service.listDirectory(p).then(setBrowse).catch(() => setBrowse(null));
  };
  const [dlg, setDlg] = useState<{ mode: "prompt" | "confirm"; title: string; value?: string; onOk: (v: string) => void } | null>(null);
  const [dlgInput, setDlgInput] = useState("");
  const folderOf = (sid: string) => sessions.find((x) => x.sessionId === sid)?.workspaceId ?? "";
  const setFolder = (sid: string, wsId: string) => {
    void service.assignSessionWorkspace({
      tenantId: service.identity.tenantId,
      sessionId: sid,
      requestId: `ws-${Date.now()}`,
      workspaceId: wsId,
    }).then(() => { refreshWs(); void refresh(); });
  };
  const [wsMenu, setWsMenu] = useState<string | null>(null);
  const [editWs, setEditWs] = useState<{ wsId: string; name: string; paths: string[]; primary: string } | null>(null);
  const [pinned, setPinned] = useState<string[]>(() => {
    try { return JSON.parse(localStorage.getItem("tsagent:pinned-ws") ?? "[]") as string[]; } catch { return []; }
  });
  const togglePin = (wsId: string) => {
    const next = pinned.includes(wsId) ? pinned.filter((x) => x !== wsId) : [...pinned, wsId];
    setPinned(next);
    try { localStorage.setItem("tsagent:pinned-ws", JSON.stringify(next)); } catch { /* quota */ }
  };
  const openEditWs = (wsId: string) => {
    const w = wsList.find((x) => x.workspaceId === wsId);
    setEditWs({
      wsId,
      name: w?.displayName ?? "",
      paths: w?.paths?.length ? [...w.paths] : w?.path ? [w.path] : [],
      primary: w?.path ?? w?.paths?.[0] ?? "",
    });
  };
  const removeWorkspace = (wsId: string, label: string) => {
    setDlg({ mode: "confirm", title: `删除工作区「${label}」？`, onOk: () => {
      void service.deleteWorkspace({
        tenantId: service.identity.tenantId,
        sessionId: service.identity.sessionId, requestId: `ws-${Date.now()}`,
        workspaceId: wsId,
      }).then(() => { refreshWs(); void refresh(); });
    } });
  };
  const folderNames = [
    ...pinned.filter((p) => wsList.some((w) => w.workspaceId === p && w.path)),
    ...wsList.filter((w) => w.path && !pinned.includes(w.workspaceId)).map((w) => w.workspaceId),
  ];
  const byFolder = (name: string) => filtered.filter((s) => folderOf(s.sessionId) === name);
  const [showReason, setShowReason] = useState(false);

  const sessionIdRef = useRef(sessionId);
  useEffect(() => { sessionIdRef.current = sessionId; }, [sessionId]);
  const runsRef = useRef(runs);
  useEffect(() => { runsRef.current = runs; }, [runs]);
  const refreshGate = useRef(new RefreshGate());
  // StrictMode 会 setup→cleanup→setup：gate 在每次 setup 新建，cleanup 只
  // 关闭当次实例 — 第二次 setup 不复用已永久关闭的 gate。须先于下方
  // refresh interval 的 effect 声明，保证首个 refresh 拿到当前实例。
  useEffect(() => {
    const gate = new RefreshGate();
    refreshGate.current = gate;
    return () => gate.close();
  }, []);

  const refresh = useCallback(async () => {
    const sid = sessionId;
    const gate = refreshGate.current;
    const seq = gate.begin();
    try {
      const snap = await fetchSessionSnapshot(service, sid);
      // 会话已切换、有更新的刷新或实例已被 cleanup 替换 — 统一提交。
      if (refreshGate.current !== gate || !gate.isCurrent(seq) || sessionIdRef.current !== snap.sessionId) return;
      setSessions(snap.sessions);
      if (!pickedSessionRef.current && snap.sessions.length > 0 && !snap.sessions.some((x) => x.sessionId === sid)) {
        const latest = snap.sessions.slice().sort((a, b) => (b.latestUpdatedAt ?? "").localeCompare(a.latestUpdatedAt ?? ""))[0];
        selectSession(latest.sessionId);
      }
      pickedSessionRef.current = true; setApprovals(snap.approvals); setParticipants(snap.participants);
      snap.sessions.slice(0, 20).forEach((x) => {
        if (x.latestRunId && !runsRef.current.find((r) => r.runId === x.latestRunId)) {
          void ctx.controller.refreshRun(x.latestRunId, x.sessionId);
        }
      });
    } catch { /* non-fatal */ }
  }, [service, sessionId]);
  useEffect(() => { void refresh(); refreshWs(); const t = setInterval(() => void refresh(), 8000); return () => clearInterval(t); }, [refresh]);
  // hydrate ALL runs in the selected session — the thread renders every
  // run's messages, so each one needs its conversation/events loaded.
  useEffect(() => {
    let dead = false;
    void (async () => {
      try {
        const snaps = await service.listRuns();
        if (dead) return;
        for (const snap of snaps.filter((x) => x.sessionId === sessionId)) {
          if (!runs.find((r) => r.runId === snap.runId)) {
            void ctx.controller.refreshRun(snap.runId, sessionId).catch(() => {});
          }
        }
      } catch { /* offline */ }
    })();
    return () => { dead = true; };
  }, [sessionId]);
  useEffect(() => {
    const onAttach = (e: Event) => {
      const { runId, files } = (e as CustomEvent).detail ?? {};
      if (runId && Array.isArray(files)) attachMap.set(runId, files);
    };
    window.addEventListener("tsagent:run-attachments", onAttach);
    return () => window.removeEventListener("tsagent:run-attachments", onAttach);
  }, [attachMap]);
  useEffect(() => {
    const onNew = () => void newSession();
    window.addEventListener("tsagent:new-session", onNew);
    return () => window.removeEventListener("tsagent:new-session", onNew);
  });

  const newSession = async () => {
    const s = await service.createSession({
      tenantId: service.identity.tenantId, userId: service.identity.userId,
      sessionId: `session-${Date.now().toString(36)}`, requestId: `sun-new-${Date.now()}`,
    });
    await refresh(); selectSession(s.sessionId);
    return s;
  };

  // session → latest run request as display title (real data)
  const titleOf = useCallback((sid: string, latestRunId?: string) => {
    const rs = runs.filter((x) => x.sessionId === sid);
    const first = rs.slice().sort((a, b) => (a.createdAt ?? "").localeCompare(b.createdAt ?? ""))[0];
    const r = first ?? rs.find((x) => x.runId === latestRunId);
    return r?.request?.slice(0, 28) ?? `会话 ${sid.slice(-6)}`;
  }, [runs]);

  const viewRun = useMemo(() => {
    const s = sessions.find((x) => x.sessionId === sessionId);
    if (ctx.activeRun?.sessionId === sessionId) return ctx.activeRun;
    return runs.find((r) => r.runId === s?.latestRunId) ?? runs.filter((r) => r.sessionId === sessionId).slice(-1)[0];
  }, [sessionId, sessions, runs, ctx.activeRun]);
  // live-refresh the viewed run — poll while active/blocked/pending so
  // output & conversation actually appear without switching sessions.
  useEffect(() => {
    const rid = viewRun?.runId;
    if (!rid) return;
    const tick = () => void ctx.controller.refreshRun(rid, sessionId).catch(() => {});
    const live = viewRun.status === "active" || viewRun.status === "blocked" || viewRun.status === "pending";
    if (!live) return;
    const t = setInterval(tick, 3000);
    return () => clearInterval(t);
  }, [viewRun?.runId, viewRun?.status, sessionId, ctx.controller]);

  const wfSteps = useMemo(() => (viewRun?.workflows ?? []).flatMap((w) => w.stages.map((st) => ({ stage: st.name, wf: w.name, status: st.status, duration: st.duration }))), [viewRun]);
  const activeCount = runs.filter((r) => r.status === "active").length;
  const isBlocked = viewRun?.status === "blocked";
  const filtered = sessions
    .slice()
    .sort((a, b) => (b.latestUpdatedAt ?? "").localeCompare(a.latestUpdatedAt ?? ""))
    .filter((s) => !query || titleOf(s.sessionId, s.latestRunId).includes(query) || s.sessionId.includes(query));

  return (
    <WorkspaceFrame scene={scene} ctx={ctx}>
      <div className="omni3">
        {/* ── Session sidebar ── */}
        <aside className="omni-rail">
          <div className="omni-rail-top">
            <div className="omni-rail-head">
              <span className="omni-eyebrow">{t("sun.sessions")}</span>
              <div className="omni-rail-icons">
                <button className={`rail-ico ${searchOpen ? "on" : ""}`} onClick={() => setSearchOpen((o) => !o)} title={t("sun.search")} type="button"><Icon name="search" size={13} /></button>
                <button className="rail-ico" onClick={() => void pickFolder().then(async (picked) => {
                  if (!picked) return;
                  await service.upsertWorkspace({
                    tenantId: service.identity.tenantId,
                    sessionId: service.identity.sessionId, requestId: `ws-${Date.now()}`,
                    workspaceId: picked.path, displayName: picked.name, path: picked.path,
                  });
                  setFolder(sessionId, picked.path);   // assign session to the workspace
                })} title={t("sun.pick-workspace")} type="button"><Icon name="folder" size={13} /></button>
                <button className="rail-ico accent" onClick={() => void newSession()} title={t("sun.new-task")} type="button"><Icon name="plus" size={13} /></button>
              </div>
            </div>
            {searchOpen && <div className="omni-search"><Icon name="search" size={12} /><input autoFocus onChange={(e) => setQuery(e.target.value)} placeholder={t("sun.search-sessions")} value={query} /></div>}
          </div>
          <div className="omni-sess-scroll">
            {(() => {
              const allSids = [service.identity.sessionId, ...filtered.map((s) => s.sessionId)].filter((v, i, a) => a.indexOf(v) === i);
              const row = (sid: string) => {
                const s = sessions.find((x) => x.sessionId === sid);
                const stRaw = s?.latestStatus ?? "";
                const st = tk(`status.${stRaw}`) ?? stRaw.toUpperCase();
                return (
                  <button className={`omni-sess3 ${sid === sessionId ? "on" : ""}`} key={sid} onClick={() => {
                    selectSession(sid);
                    const rid = s?.latestRunId;
                    if (rid && !runs.find((r) => r.runId === rid)) void ctx.controller.refreshRun(rid, sid);
                  }} onContextMenu={(e) => { e.preventDefault(); setDlgInput(folderOf(sid)); setDlg({ mode: "prompt", title: t("sun.folder-hint"), value: folderOf(sid), onOk: (n) => setFolder(sid, n) }); }}
                    draggable onDragStart={(e) => { setDragSid(sid); e.dataTransfer.effectAllowed = "move"; }} onDragEnd={() => setDragSid(null)}
                    type="button">
                    <span className={`omni-sess-dot ${stRaw === "blocked" || stRaw === "failed" ? "blocked" : stRaw === "active" ? "active" : ""}`} />
                    <span className="omni-sess-info">
                      <strong>{titleOf(sid, s?.latestRunId)}</strong>
                      <small>{st && <em className={`sess-st ${stRaw.toLowerCase()}`}>{st}</em>} {rel(s?.latestUpdatedAt ?? "")}</small>
                    </span>
                    <span className="omni-sess-del" onClick={(e) => {
                      e.stopPropagation();
                      setDlg({ mode: "confirm", title: t("sun.delete-session-q", { title: titleOf(sid, s?.latestRunId) }), onOk: () => {
                        void service.deleteSession({ tenantId: service.identity.tenantId, userId: service.identity.userId, sessionId: sid, requestId: `req-${Date.now()}` })
                          .then(() => {
                            const next = sessions.find((x) => x.sessionId !== sid);
                            if (sid === sessionId) selectSession(next?.sessionId ?? service.identity.sessionId);
                            void refresh();
                          })
                          .catch(() => {});
                      } });
                    }} title={t("sun.delete-session")}>✕</span>
                  </button>
                );
              };
              const unfiled = allSids.filter((sid) => !folderOf(sid));
              return (
                <>
                  {folderNames.map((name) => {
                    const inside = allSids.filter((sid) => folderOf(sid) === name);
                    const ws = wsList.find((w) => w.workspaceId === name);
                    const label = ws?.displayName || name.split("/").filter(Boolean).pop() || name;
                    return (
                      <div className={`omni-folder ${dragSid ? "droppable" : ""}`} key={name}
                        onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; }}
                        onDrop={(e) => { e.preventDefault(); if (dragSid) setFolder(dragSid, name); setDragSid(null); }}>
                        <span className="omni-group-label folder-head" onClick={() => setCollapsed((c) => { const n = new Set(c); n.has(name) ? n.delete(name) : n.add(name); return n; })}>
                          {collapsed.has(name) ? "▸" : "▾"} {label}
                          <span className="folder-acts" onClick={(e) => e.stopPropagation()}>
                            <i title={t("sun.new-in-folder")} onClick={() => void newSession().then((ns) => { if (ns?.sessionId) setFolder(ns.sessionId, name); })}>＋</i>
                            <i title={t("sun.options")} onClick={() => setWsMenu(wsMenu === name ? null : name)}>···</i>
                          </span>
                        </span>
                        {wsMenu === name && (
                          <>
                            <div className="ws-menu-mask" onClick={() => setWsMenu(null)} />
                            <div className="ws-menu">
                              <button onClick={() => { togglePin(name); setWsMenu(null); }} type="button">
                                {pinned.includes(name) ? t("sun.unpin") : t("sun.pin")}
                              </button>
                              <button onClick={() => { openEditWs(name); setWsMenu(null); }} type="button">{t("sun.edit")}</button>
                              <button onClick={() => { if (ws?.path) void service.revealPath(ws.path); setWsMenu(null); }} type="button">{t("sun.reveal")}</button>
                              <button className="danger" onClick={() => { setWsMenu(null); removeWorkspace(name, label); }} type="button">{t("sun.remove-project")}</button>
                            </div>
                          </>
                        )}
                        {!collapsed.has(name) && inside.map(row)}
                        {!collapsed.has(name) && inside.length === 0 && <span className="omni-folder-empty">{t("sun.drag-here")}</span>}
                      </div>
                    );
                  })}
                  <span className="omni-group-label"
                    onDragOver={(e) => e.preventDefault()}
                    onDrop={(e) => { e.preventDefault(); if (dragSid) setFolder(dragSid, ""); setDragSid(null); }}>{t("sun.recent")}</span>
                  {unfiled.map(row)}
                </>
              );
            })()}
            {filtered.length === 0 && query && <p className="omni-empty">{t("sun.no-matching")}</p>}
          </div>
        </aside>

        {browse && (
          <div className="omni-dlg-mask" onClick={() => setBrowse(null)}>
            <div className="omni-dlg omni-browse" onClick={(e) => e.stopPropagation()}>
              <p className="omni-dlg-title">{t("sun.pick-dir")}</p>
              <div className="browse-path" title={browse.path}>{browse.path}</div>
              <div className="browse-list">
                {browse.parent !== browse.path && (
                  <button className="browse-row" onClick={() => openBrowse(browse.parent)} type="button">..</button>
                )}
                {browse.dirs.map((d) => (
                  <button className="browse-row" key={d} onClick={() => openBrowse(browse.path + "/" + d.slice(0, -1))} type="button">📁 {d}</button>
                ))}
                {browse.dirs.length === 0 && <span className="browse-empty">{t("sun.no-subdir")}</span>}
              </div>
              <div className="omni-dlg-btns">
                <button className="omni-dlg-cancel" onClick={() => setBrowse(null)} type="button">{t("common.cancel")}</button>
                <button className="omni-dlg-ok" onClick={() => {
                  void service.upsertWorkspace({
                    tenantId: service.identity.tenantId,
                    sessionId: service.identity.sessionId, requestId: `ws-${Date.now()}`,
                    workspaceId: browse.path, displayName: browse.path.split("/").filter(Boolean).pop() ?? browse.path, path: browse.path,
                  }).then(() => { setFolder(sessionId, browse.path); setBrowse(null); });
                }} type="button">{t("sun.pick-this")}</button>
              </div>
            </div>
          </div>
        )}
        {editWs && (
          <div className="omni-dlg-mask" onClick={() => setEditWs(null)}>
            <div className="omni-dlg omni-wsedit" onClick={(e) => e.stopPropagation()}>
              <p className="omni-dlg-title">{t("sun.edit-project")}</p>
              <input className="omni-dlg-input" onChange={(e) => setEditWs({ ...editWs, name: e.target.value })} value={editWs.name} />
              <div className="ws-src-title">Source folders</div>
              <div className="ws-src-list">
                {editWs.paths.map((p) => (
                  <div className="ws-src-row" key={p} onClick={() => setEditWs({ ...editWs, primary: p })} title={t("sun.set-primary")}>
                    <Icon name="folder" size={12} />
                    <span className="ws-src-path" title={p}>{p.split("/").filter(Boolean).pop() ?? p}</span>
                    {p === editWs.primary && <em className="ws-src-primary">{t("sun.primary")}</em>}
                    <i className="ws-src-del" onClick={(e) => {
                      e.stopPropagation();
                      const rest = editWs.paths.filter((x) => x !== p);
                      setEditWs({ ...editWs, paths: rest, primary: editWs.primary === p ? (rest[0] ?? "") : editWs.primary });
                    }}>✕</i>
                  </div>
                ))}
                <button className="ws-src-add" onClick={() => void pickFolder().then((picked) => {
                  if (picked && !editWs.paths.includes(picked.path)) {
                    setEditWs({ ...editWs, paths: [...editWs.paths, picked.path], primary: editWs.primary || picked.path });
                  }
                })} type="button">{t("sun.add-folder")}</button>
              </div>
              <div className="omni-dlg-btns ws-edit-btns">
                <button className="ws-remove" onClick={() => {
                  const label = editWs.name || editWs.wsId;
                  const wsId = editWs.wsId;
                  setEditWs(null);
                  removeWorkspace(wsId, label);
                }} type="button">{t("sun.remove-local")}</button>
                <button className="omni-dlg-cancel" onClick={() => setEditWs(null)} type="button">{t("common.cancel")}</button>
                <button className="omni-dlg-ok" onClick={() => {
                  void service.upsertWorkspace({
                    tenantId: service.identity.tenantId,
                    sessionId: service.identity.sessionId, requestId: `ws-${Date.now()}`,
                    workspaceId: editWs.wsId, displayName: editWs.name.trim(),
                    path: editWs.primary, paths: editWs.paths,
                  }).then(() => { refreshWs(); setEditWs(null); });
                }} type="button">{t("common.save")}</button>
              </div>
            </div>
          </div>
        )}
        {dlg && (
          <div className="omni-dlg-mask" onClick={() => setDlg(null)}>
            <div className="omni-dlg" onClick={(e) => e.stopPropagation()}>
              <p className="omni-dlg-title">{dlg.title}</p>
              {dlg.mode === "prompt" && (
                <input autoFocus className="omni-dlg-input" onChange={(e) => setDlgInput(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") { dlg.onOk(dlgInput); setDlg(null); } if (e.key === "Escape") setDlg(null); }}
                  value={dlgInput} />
              )}
              <div className="omni-dlg-btns">
                <button className="omni-dlg-cancel" onClick={() => setDlg(null)} type="button">{t("common.cancel")}</button>
                <button className="omni-dlg-ok" onClick={() => { dlg.onOk(dlgInput); setDlg(null); }} type="button">
                  {dlg.mode === "confirm" ? t("common.delete") : t("common.confirm")}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* ── Main column ── */}
        <main className="omni-col">
          {/* pending approval — only chrome that still earns its place */}
          {isBlocked && approvals.length > 0 && (
            <section className="omni-taskbar blocked">
              <div className="appr-detail">
                <span className="omni-warn">⚠ {approvals[0].action} — {String(approvals[0].params?.path ?? approvals[0].scope)}</span>
                <code className="appr-params">{JSON.stringify(approvals[0].params ?? {}, null, 1).slice(0, 400)}</code>
                {approvals[0].riskLevel && <span className="appr-risk">{t("sun.risk")} {String(approvals[0].riskLevel)}</span>}
              </div>
              <button className="btn primary" onClick={() => void service.decideApproval({ tenantId: service.identity.tenantId, sessionId: service.identity.sessionId, requestId: requestIdForRun(0), runId: approvals[0].runId, approvalId: approvals[0].approvalId, decision: "approve", decidedBy: service.identity.userId }).then(refresh)} type="button">{t("sun.approve")}</button>
              <button className="btn danger" onClick={() => void service.decideApproval({ tenantId: service.identity.tenantId, sessionId: service.identity.sessionId, requestId: requestIdForRun(0), runId: approvals[0].runId, approvalId: approvals[0].approvalId, decision: "deny", decidedBy: service.identity.userId }).then(refresh)} type="button">{t("sun.deny")}</button>
            </section>
          )}

          {/* Chat column — request bubble + streamed/final reply */}
          <div className="omni-thread3">
            {!viewRun && <OmniLanding />}
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
                    {(attachMap.get(r.runId) ?? []).length > 0 && (
                      <div className="attach-row">
                        {attachMap.get(r.runId)!.map((f, i) => (
                          <span className="attach-chip" key={i} title={f}>📎 {f.split("/").pop()}</span>
                        ))}
                      </div>
                    )}
                  </article>
                )}
                <ThinkingChain events={r.events} messages={thinkingMsgs} />
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
                <div className="msg3-head"><span className="mod-title">{t("sun.current-execution")}</span><em>{statusLabel(viewRun!.status)}</em></div>
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

            {/* TASK OUTPUTS — generated vs pending, hover shows preview affordance */}
            {(viewRun?.artifacts ?? []).length > 0 && (
              <article className="msg3 artifacts mech">
                <div className="msg3-head"><span className="mod-title">{t("sun.task-outputs")}</span><em></em></div>
                <div className="artifact-rows">
                  {viewRun!.artifacts.map((a) => (
                    <button className={`artifact-row ${a.status}`} key={a.artifactId} title={a.path} type="button">
                      <strong>📄 {a.path.split("/").pop()}</strong>
                      <small>{a.path.split(".").pop()?.toUpperCase()}</small>
                      <em className={a.status === "generated" ? "" : "pending"}>{a.status}</em>
                      <span className="artifact-acts">{t("sun.preview-forge")}</span>
                    </button>
                  ))}
                </div>
              </article>
            )}
            {viewRun?.failure && !isBlocked && <article className="msg3 error"><div className="msg3-body">{viewRun.failure.message}</div></article>}
          </div>

          {workspaceOf(sessionId) && <div className="ws-bound" title={workspaceOf(sessionId)}>📁 {t("sun.bound-to")}: {workspaceOf(sessionId).split("/").pop()}<i onClick={() => setFolder(sessionId, "")}>✕</i></div>}<AgentComposer ctx={{ ...ctx, workspacePath: workspaceOf(sessionId) }} planet="sun" placeholder={t("sun.composer-placeholder")} />

          {/* status footer — real counts */}
          <footer className="omni-statusbar">
            <span className="sys-ok">● SYSTEM ONLINE</span>
            <span>{t("footer.running", { n: activeCount })} · {t("footer.waiting", { n: runs.filter((r) => r.status === "blocked").length })}</span>
            <span>{t("footer.pending-approval", { n: approvals.length })}</span>
            <span className="omni-status-right" title={participants.map((x) => `${x.role}:${x.participantId}`).join(" ") + (participants.length ? " · " : "") + sessionId}>{participants.length > 0 ? `👥 ${participants.map((x) => `${x.role}:${x.participantId.slice(0,12)}`).join(" ")} · ` : ""}{sessionId}</span>
          </footer>
        </main>
      </div>
    </WorkspaceFrame>
  );
}
