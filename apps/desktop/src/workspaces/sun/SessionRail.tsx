import { useState } from "react";
import { SCENES } from "../../app/scenes";
import { pickFolder } from "../../service/tauriBridge";
import { Icon, rel } from "../../app/ui";
import { t, tk } from "../../app/i18n";
import { isOmniSession } from "../../app/sessionWorkspace";
import type { RunCtx } from "../../app/runCtx";
import type { SessionSummary, WorkspaceSummary } from "../../types/service";

interface Props {
  ctx: RunCtx;
  sessions: SessionSummary[];
  titleOf: (sid: string, latestRunId?: string) => string;
  wsList: WorkspaceSummary[];
  onNewSession: () => Promise<SessionSummary>;
  onSetFolder: (sid: string, wsId: string) => void;
  onSessionsChanged: () => void;
  openPrompt: (title: string, value: string, onOk: (v: string) => void) => void;
  openConfirm: (title: string, onOk: () => void) => void;
  onEditWorkspace: (wsId: string) => void;
  onRemoveWorkspace: (wsId: string, label: string) => void;
}

/** Session sidebar — search, workstation filter, folder grouping, drag to
 *  assign a session to a workspace, workspace context menu. All rail-only
 *  UI state (query/filter/collapse/drag/menu) lives here. */
export default function SessionRail({ ctx, sessions, titleOf, wsList, onNewSession, onSetFolder, onSessionsChanged, openPrompt, openConfirm, onEditWorkspace, onRemoveWorkspace }: Props) {
  const { service, sessionId } = ctx;
  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  // Omni lists its own records by default; other planets stay an explicit
  // opt-in so workstation history never leaks into the aggregate view
  // unprompted.
  const [planetFilter, setPlanetFilter] = useState<string>("");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [dragSid, setDragSid] = useState<string | null>(null);
  const [wsMenu, setWsMenu] = useState<string | null>(null);
  const [pinned, setPinned] = useState<string[]>(() => {
    try { return JSON.parse(localStorage.getItem("tsagent:pinned-ws") ?? "[]") as string[]; } catch { return []; }
  });
  const togglePin = (wsId: string) => {
    const next = pinned.includes(wsId) ? pinned.filter((x) => x !== wsId) : [...pinned, wsId];
    setPinned(next);
    try { localStorage.setItem("tsagent:pinned-ws", JSON.stringify(next)); } catch { /* quota */ }
  };

  const folderOf = (sid: string) => sessions.find((x) => x.sessionId === sid)?.workspaceId ?? "";
  const folderNames = [
    ...pinned.filter((p) => wsList.some((w) => w.workspaceId === p && w.path)),
    ...wsList.filter((w) => w.path && !pinned.includes(w.workspaceId)).map((w) => w.workspaceId),
  ];
  const filtered = sessions
    .slice()
    .sort((a, b) => (b.latestUpdatedAt ?? "").localeCompare(a.latestUpdatedAt ?? ""))
    .filter((s) => planetFilter === "all" || (planetFilter === "" ? isOmniSession(s) : (s.planet ?? "") === planetFilter))
    .filter((s) => !query || titleOf(s.sessionId, s.latestRunId).includes(query) || s.sessionId.includes(query));

  const pickWorkspace = () => void pickFolder().then(async (picked) => {
    if (!picked) return;
    await service.upsertWorkspace({
      sessionId: ctx.sessionId, workspaceId: picked.path, displayName: picked.name, path: picked.path,
    });
    onSetFolder(sessionId, picked.path);   // assign session to the workspace
  }).catch(ctx.reportError);

  return (
    <aside className="omni-rail">
      <div className="omni-rail-top">
        <div className="omni-rail-head">
          <span className="omni-eyebrow">{t("sun.sessions")}</span>
          <div className="omni-rail-icons">
            <button className={`rail-ico ${searchOpen ? "on" : ""}`} onClick={() => setSearchOpen((o) => !o)} title={t("sun.search")} type="button"><Icon name="search" size={13} /></button>
            <button className="rail-ico" onClick={pickWorkspace} title={t("sun.pick-workspace")} type="button"><Icon name="folder" size={13} /></button>
            <button className="rail-ico accent" onClick={() => void onNewSession().catch(ctx.reportError)} title={t("sun.new-task")} type="button"><Icon name="plus" size={13} /></button>
          </div>
        </div>
        {searchOpen && <div className="omni-search"><Icon name="search" size={12} /><input autoFocus onChange={(e) => setQuery(e.target.value)} placeholder={t("sun.search-sessions")} value={query} /></div>}
        <div className="omni-search omni-planet-filter">
          <select onChange={(e) => setPlanetFilter(e.target.value)} value={planetFilter}>
            <option value="all">{t("sun.filter-all")}</option>
            <option value="">{t("sun.filter-omni")}</option>
            {SCENES.map((sc) => (
              <option key={sc.id} value={sc.id}>{sc.workspace}</option>
            ))}
          </select>
        </div>
      </div>
      <div className="omni-sess-scroll">
        {(() => {
          const allSids = [ctx.sessionId, ...filtered.map((s) => s.sessionId)].filter((v, i, a) => a.indexOf(v) === i);
          const row = (sid: string) => {
            const s = sessions.find((x) => x.sessionId === sid);
            const stRaw = s?.latestStatus ?? "";
            const st = tk(`status.${stRaw}`) ?? stRaw.toUpperCase();
            return (
              <button className={`omni-sess3 ${sid === sessionId ? "on" : ""}`} key={sid} onClick={() => {
                ctx.selectSession(sid);
                const rid = s?.latestRunId;
                if (rid && !ctx.runs.find((r) => r.runId === rid)) void ctx.controller.refreshRun(rid, sid).catch(() => {});
              }} onContextMenu={(e) => { e.preventDefault(); openPrompt(t("sun.folder-hint"), folderOf(sid), (n) => onSetFolder(sid, n)); }}
                draggable onDragStart={(e) => { setDragSid(sid); e.dataTransfer.effectAllowed = "move"; }} onDragEnd={() => setDragSid(null)}
                type="button">
                <span className={`omni-sess-dot ${stRaw === "blocked" || stRaw === "failed" ? "blocked" : stRaw === "active" ? "active" : ""}`} />
                <span className="omni-sess-info">
                  <strong>{titleOf(sid, s?.latestRunId)}</strong>
                  <small>{st && <em className={`sess-st ${stRaw.toLowerCase()}`}>{st}</em>} {rel(s?.latestUpdatedAt ?? "")}</small>
                </span>
                <span className="omni-sess-del" onClick={(e) => {
                  e.stopPropagation();
                  openConfirm(t("sun.delete-session-q", { title: titleOf(sid, s?.latestRunId) }), () => {
                    void service.deleteSession({ sessionId: sid })
                      .then(() => {
                        const next = sessions.find((x) => x.sessionId !== sid);
                        if (sid === sessionId) ctx.selectSession(next?.sessionId ?? ctx.sessionId);
                        onSessionsChanged();
                      })
                      .catch(ctx.reportError);
                  });
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
                    onDrop={(e) => { e.preventDefault(); if (dragSid) onSetFolder(dragSid, name); setDragSid(null); }}>
                    <span className="omni-group-label folder-head" onClick={() => setCollapsed((c) => { const n = new Set(c); n.has(name) ? n.delete(name) : n.add(name); return n; })}>
                      {collapsed.has(name) ? "▸" : "▾"} {label}
                      <span className="folder-acts" onClick={(e) => e.stopPropagation()}>
                        <i title={t("sun.new-in-folder")} onClick={() => void onNewSession().then((ns) => { if (ns?.sessionId) onSetFolder(ns.sessionId, name); })}>＋</i>
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
                          <button onClick={() => { onEditWorkspace(name); setWsMenu(null); }} type="button">{t("sun.edit")}</button>
                          <button onClick={() => { if (ws?.path) void service.revealPath(ws.path); setWsMenu(null); }} type="button">{t("sun.reveal")}</button>
                          <button className="danger" onClick={() => { setWsMenu(null); onRemoveWorkspace(name, label); }} type="button">{t("sun.remove-project")}</button>
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
                onDrop={(e) => { e.preventDefault(); if (dragSid) onSetFolder(dragSid, ""); setDragSid(null); }}>{t("sun.recent")}</span>
              {unfiled.map(row)}
            </>
          );
        })()}
        {filtered.length === 0 && query && <p className="omni-empty">{t("sun.no-matching")}</p>}
      </div>
    </aside>
  );
}
