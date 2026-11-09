import { useCallback, useEffect, useRef, useState } from "react";
import type { RunCtx } from "./runCtx";
import { Icon } from "./ui";
import { delegateToPlanet } from "./delegate";
import type { SessionSummary } from "../types/service";

/** Global Chat — §4.1 honest subset: sessions are real (list/create), a sent
 *  message starts a Run in the active session (TSAgent's run-scoped chat
 *  model); @Agent delegation lands via the Sun orchestration surface. */
/** Session folders — UI-local grouping, persisted in localStorage (not a
 *  backend domain; groups only live on this desktop). */
type FolderMap = Record<string, string>;
const FOLDERS_KEY = "tsa.session-folders";
const MAP_KEY = "tsa.session-folder-map";
const loadJson = <T,>(k: string, d: T): T => { try { return JSON.parse(localStorage.getItem(k) ?? "") ?? d; } catch { return d; } };

export default function GlobalChat({ ctx, onClose, embedded = false, instant = false }: { ctx: RunCtx; onClose?: () => void; embedded?: boolean; instant?: boolean }) {
  const { service, sessionId, selectSession, activeRun, requestDraft, setRequestDraft, createRun } = ctx;
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [delegateTo, setDelegateTo] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const threadRef = useRef<HTMLDivElement>(null);

  const [creating, setCreating] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [folders, setFolders] = useState<Record<string, string>>(() => loadJson(FOLDERS_KEY, {}));
  const [folderOf, setFolderOf] = useState<FolderMap>(() => loadJson(MAP_KEY, {}));
  const pickerRef = useRef<HTMLDivElement>(null);
  const saveFolders = (f: Record<string, string>, m: FolderMap) => {
    setFolders(f); setFolderOf(m);
    localStorage.setItem(FOLDERS_KEY, JSON.stringify(f));
    localStorage.setItem(MAP_KEY, JSON.stringify(m));
  };
  const addFolder = () => {
    const name = window.prompt("文件夹名称");
    if (!name?.trim()) return;
    saveFolders({ ...folders, [`f-${Date.now().toString(36)}`]: name.trim() }, folderOf);
  };
  const removeFolder = (id: string) => {
    const nf = { ...folders }; delete nf[id];
    const nm = { ...folderOf };
    Object.keys(nm).forEach((s) => { if (nm[s] === id) delete nm[s]; });
    saveFolders(nf, nm);
  };

  const refreshSessions = useCallback(async () => {
    try {
      setSessions(await service.listSessions({
        tenantId: service.identity.tenantId,
        userId: service.identity.userId,
        requestId: `chat-${Date.now()}`,
      }));
    } catch { /* list unavailable */ }
  }, [service]);

  useEffect(() => { void refreshSessions(); }, [refreshSessions]);
  useEffect(() => {
    threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight });
  }, [activeRun?.conversation.length]);
  useEffect(() => {
    if (!pickerOpen) return;
    const onDown = (e: MouseEvent) => { if (!pickerRef.current?.contains(e.target as Node)) setPickerOpen(false); };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [pickerOpen]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose?.(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const allSessions = [
    { sessionId, runCount: -1 },
    ...sessions.filter((x) => x.sessionId !== sessionId),
  ];

  return (
    <div className={`chat-host ${embedded ? "embed" : "dock"} ${instant && !embedded ? "instant" : ""}`}>
      <header className="chat-head">
        <div><span className="panel-eyebrow">AGENT DOCK</span><strong>{activeRun?.runId ?? "no-run"}</strong></div>
        <div className="sess-picker" ref={pickerRef}>
          <button className="sess-pick-btn" onClick={() => setPickerOpen((v) => !v)} type="button">
            {folderOf[sessionId] ? `${folders[folderOf[sessionId]] ?? ""} / ` : ""}{sessionId} ▾
          </button>
          {pickerOpen && (
            <div className="sess-panel">
              <div className="sess-actions">
                <button className="btn secondary" onClick={async () => {
                  setCreating(true);
                  try {
                    const s = await service.createSession({
                      tenantId: service.identity.tenantId, userId: service.identity.userId,
                      sessionId: `session-${Date.now().toString(36)}`,
                      requestId: `chat-new-${Date.now()}`,
                    });
                    await refreshSessions(); selectSession(s.sessionId); setPickerOpen(false);
                  } catch (err) { setNotice(String(err)); } finally { setCreating(false); }
                }} disabled={creating} type="button">{creating ? "…" : "+ session"}</button>
                <button className="btn secondary" onClick={addFolder} type="button">+ 文件夹</button>
              </div>
              {Object.entries(folders).map(([fid, name]) => (
                <div className="sess-folder" key={fid}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={(e) => {
                    e.preventDefault();
                    const sid = e.dataTransfer.getData("text/session");
                    if (sid) saveFolders(folders, { ...folderOf, [sid]: fid });
                  }}>
                  <div className="sess-folder-head">
                    <span>▸ {name}</span>
                    <button className="sess-folder-x" onClick={() => removeFolder(fid)} type="button">×</button>
                  </div>
                  {allSessions.filter((s) => folderOf[s.sessionId] === fid).map((s) => (
                    <SessionRow key={s.sessionId} s={s} cur={sessionId} onPick={(id) => { selectSession(id); setPickerOpen(false); }} />
                  ))}
                </div>
              ))}
              <div className="sess-folder ungrouped"
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  e.preventDefault();
                  const sid = e.dataTransfer.getData("text/session");
                  if (sid) { const m = { ...folderOf }; delete m[sid]; saveFolders(folders, m); }
                }}>
                <div className="sess-folder-head"><span>未分组</span></div>
                {allSessions.filter((s) => !folderOf[s.sessionId]).map((s) => (
                  <SessionRow key={s.sessionId} s={s} cur={sessionId} onPick={(id) => { selectSession(id); setPickerOpen(false); }} />
                ))}
              </div>
            </div>
          )}
        </div>
        <select className="chat-target" value={delegateTo} onChange={(e) => setDelegateTo(e.target.value)} title="Delegate to @planet">
          <option value="">@direct</option>
          {["mercury","venus","earth","mars","jupiter","saturn","uranus","neptune","pluto"].map((p) => (
            <option key={p} value={p}>@{p}</option>
          ))}
        </select>
        {!embedded && <button className="modal-close" onClick={() => onClose?.()} type="button"><Icon name="x" size={15} /></button>}
      </header>

      <div className="chat-thread" ref={threadRef}>
        {(activeRun?.conversation ?? []).map((m) => (
          <article className={`mc-msg ${m.role}`} key={m.messageId}>
            <div className="mc-msg-head"><span>{m.role === "user" ? "YOU" : "T.S.A"}</span><time>{m.at}</time></div>
            <div className="mc-msg-body">{m.content}</div>
          </article>
        ))}
        {activeRun?.output && (
          <article className="mc-msg assistant out">
            <div className="mc-msg-head"><span>T.S.A · OUTPUT</span><time>{activeRun.output.createdAt}</time></div>
            <div className="mc-msg-body">{activeRun?.output.text}</div>
          </article>
        )}
      </div>

      <form className="console-composer" onSubmit={async (e) => {
        e.preventDefault();
        if (delegateTo) {
          const text = requestDraft.trim();
          if (!text) return;
          try {
            await delegateToPlanet(ctx, delegateTo, text);
            setRequestDraft("");
            setNotice(`delegated → ${delegateTo}`);
          } catch (err) { setNotice(String(err)); }
        } else {
          createRun(e);
          onClose?.();
        }
      }}>
        <span className="dock-glyph"><Icon name="terminal" size={13} /></span>
        <textarea autoFocus onChange={(e) => setRequestDraft(e.target.value)}
          placeholder="Message — starts a Run in this session…" rows={2} value={requestDraft} />
        <button className="mc-send" title="Send (start run)" type="submit"><Icon name="send" size={14} /></button>
      </form>
      {notice && <p className="chat-foot" style={{ color: "#5fe0b1" }}>{notice}</p>}
      <p className="chat-foot">Messages start Runs in <code>{sessionId}</code> · @planet delegates a child Task+Run · ⌘L to reopen</p>
    </div>
  );
}

function SessionRow({ s, cur, onPick }: { s: { sessionId: string; runCount: number }; cur: string; onPick: (id: string) => void }) {
  return (
    <button className={`sess-row ${cur === s.sessionId ? "on" : ""}`} draggable
      onDragStart={(e) => e.dataTransfer.setData("text/session", s.sessionId)}
      onClick={() => onPick(s.sessionId)} type="button">
      <span className="sess-id">{s.sessionId}</span>
      {s.runCount >= 0 && <span className="sess-n">{s.runCount} runs</span>}
    </button>
  );
}
