import { useCallback, useEffect, useRef, useState } from "react";
import type { RunCtx } from "./runCtx";
import { Icon, MarkdownText } from "./ui";
import { useScrollFollow } from "./useScrollFollow";
import { submitToPlanet } from "./delegate";
import { selectWorkspaceRun } from "./workspaceSelection";
import { isOmniSession } from "./sessionWorkspace";
import { draftKey, useDraft, clearDraftIfUnchanged } from "./draftStore";
import { t } from "./i18n";
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
  const { service, sessionId, selectSession, activeRun, createRun } = ctx;
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [delegateTo, setDelegateTo] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const thread = useScrollFollow(activeRun?.runId ?? "none");
  const composingRef = useRef(false);
  // Draft keyed by the selected target — @direct shares the Sun workspace
  // draft (same tenant/session/target key), @planet gets its own.
  const dkey = draftKey(service.identity.tenantId, sessionId, delegateTo || "sun");
  const [draft, setDraft] = useDraft(dkey);

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
    const name = window.prompt(t("chat.folder-name"));
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
  useEffect(() => { thread.onContentGrow(); }, [activeRun?.conversation.length, activeRun?.output]);
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
    // Workstation-claimed records stay on their own rail — Omni lists
    // only what it owns (unclaimed sessions plus delegated children,
    // which are unclaimed by construction).
    ...sessions.filter((x) => x.sessionId !== sessionId && isOmniSession(x)),
  ];

  return (
    <div className={`chat-host ${embedded ? "embed" : "dock"} ${instant && !embedded ? "instant" : ""}`}>
      <header className="chat-head">
        <div><span className="panel-eyebrow">{t("chat.dock")}</span><strong>{activeRun?.runId ?? t("chat.no-run")}</strong></div>
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
                }} disabled={creating} type="button">{creating ? "…" : t("chat.new-session")}</button>
                <button className="btn secondary" onClick={addFolder} type="button">{t("chat.new-folder")}</button>
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
                <div className="sess-folder-head"><span>{t("chat.ungrouped")}</span></div>
                {allSessions.filter((s) => !folderOf[s.sessionId]).map((s) => (
                  <SessionRow key={s.sessionId} s={s} cur={sessionId} onPick={(id) => { selectSession(id); setPickerOpen(false); }} />
                ))}
              </div>
            </div>
          )}
        </div>
        <select className="chat-target" value={delegateTo} onChange={(e) => setDelegateTo(e.target.value)} title={t("chat.target")}>
          <option value="">{t("chat.direct")}</option>
          {["mercury","venus","earth","mars","jupiter","saturn","uranus","neptune","pluto"].map((p) => (
            <option key={p} value={p}>@{p}</option>
          ))}
        </select>
        {!embedded && <button className="modal-close" onClick={() => onClose?.()} type="button"><Icon name="x" size={15} /></button>}
      </header>

      <div className="chat-thread" onScroll={thread.onScroll} ref={thread.ref}>
        {(activeRun?.conversation ?? []).map((m) => (
          <article className={`mc-msg ${m.role}`} key={m.messageId}>
            <div className="mc-msg-head"><span>{m.role === "user" ? t("chat.you") : "T.S.A"}</span><time>{m.at}</time></div>
            <div className="mc-msg-body"><MarkdownText text={m.content} /></div>
          </article>
        ))}
        {activeRun?.output && (
          <article className="mc-msg assistant out">
            <div className="mc-msg-head"><span>T.S.A · {t("chat.output")}</span><time>{activeRun.output.createdAt}</time></div>
            <div className="mc-msg-body"><MarkdownText text={activeRun.output.text} /></div>
          </article>
        )}
        {thread.hasNew && (
          <button className="scroll-pill" onClick={thread.toBottom} type="button">{t("thread.new-content")} ↓</button>
        )}
      </div>

      <form className="console-composer" onSubmit={async (e) => {
        e.preventDefault();
        const text = draft.text.trim();
        if (!text) return;
        const submitted = draft;
        if (delegateTo) {
          try {
            const runId = await submitToPlanet(ctx, delegateTo, text, draft.files);
            selectWorkspaceRun(ctx.service.identity.tenantId, ctx.sessionId, delegateTo, runId);
            clearDraftIfUnchanged(dkey, submitted);
            setNotice(t("composer.delegated", { target: delegateTo }));
          } catch (err) { setNotice(String(err)); }
        } else {
          createRun(e);
          onClose?.();
        }
      }}>
        <span className="dock-glyph"><Icon name="terminal" size={13} /></span>
        <textarea autoFocus onChange={(e) => setDraft({ ...draft, text: e.target.value })}
          onCompositionStart={() => { composingRef.current = true; }}
          onCompositionEnd={() => { composingRef.current = false; }}
          onKeyDown={(e) => {
            if (e.key !== "Enter" || e.shiftKey) return;
            if (e.nativeEvent.isComposing || composingRef.current) return;
            e.preventDefault();
            (e.currentTarget.form as HTMLFormElement).requestSubmit();
          }}
          placeholder={t("chat.placeholder")} rows={2} value={draft.text} />
        <button className="mc-send" title={t("chat.send-title")} type="submit"><Icon name="send" size={14} /></button>
      </form>
      {notice && <p className="chat-foot" style={{ color: "#5fe0b1" }}>{notice}</p>}
      <p className="chat-foot">{t("chat.foot", { session: sessionId })}</p>
    </div>
  );
}

function SessionRow({ s, cur, onPick }: { s: { sessionId: string; runCount: number }; cur: string; onPick: (id: string) => void }) {
  return (
    <button className={`sess-row ${cur === s.sessionId ? "on" : ""}`} draggable
      onDragStart={(e) => e.dataTransfer.setData("text/session", s.sessionId)}
      onClick={() => onPick(s.sessionId)} type="button">
      <span className="sess-id">{s.sessionId}</span>
      {s.runCount >= 0 && <span className="sess-n">{t("chat.runs", { n: s.runCount })}</span>}
    </button>
  );
}
