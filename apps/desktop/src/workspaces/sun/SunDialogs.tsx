import { useEffect, useState } from "react";
import { pickFolder } from "../../service/tauriBridge";
import { Icon } from "../../app/ui";
import { t } from "../../app/i18n";
import type { RunCtx } from "../../app/runCtx";
import type { DirectoryListing, WorkspaceSummary } from "../../types/service";

interface DlgSpec { mode: "prompt" | "confirm"; title: string; onOk: (v: string) => void }
interface WsEdit { wsId: string; name: string; paths: string[]; primary: string }

export interface SunDialogsApi {
  openBrowse: (p?: string) => void;
  openEditWs: (wsId: string) => void;
  openPrompt: (title: string, value: string, onOk: (v: string) => void) => void;
  openConfirm: (title: string, onOk: () => void) => void;
  removeWorkspace: (wsId: string, label: string) => void;
}

interface Actions {
  setFolder: (sid: string, wsId: string) => void;
  /** Workspace/session data changed — reload both lists. */
  onChanged: () => void;
}

/** Owns the three overlay dialogs (directory browser, workspace editor,
 *  generic prompt/confirm) plus Escape-to-close with focus restore. */
export function useSunDialogs(ctx: RunCtx, wsList: WorkspaceSummary[], actions: Actions) {
  const { service, sessionId } = ctx;
  const [browse, setBrowse] = useState<DirectoryListing | null>(null);
  const [editWs, setEditWs] = useState<WsEdit | null>(null);
  const [dlg, setDlg] = useState<DlgSpec | null>(null);
  const [dlgInput, setDlgInput] = useState("");

  // 弹层统一行为 — Escape 关闭任意弹层，关闭后焦点还原到触发控件。
  const anyDlg = browse !== null || editWs !== null || dlg !== null;
  useEffect(() => {
    if (!anyDlg) return;
    const prev = document.activeElement as HTMLElement | null;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setBrowse(null); setEditWs(null); setDlg(null); }
    };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); prev?.focus?.(); };
  }, [anyDlg]);

  const api: SunDialogsApi = {
    openBrowse: (p = "") => void service.listDirectory(p).then(setBrowse).catch(() => setBrowse(null)),
    openEditWs: (wsId) => {
      const w = wsList.find((x) => x.workspaceId === wsId);
      setEditWs({
        wsId,
        name: w?.displayName ?? "",
        paths: w?.paths?.length ? [...w.paths] : w?.path ? [w.path] : [],
        primary: w?.path ?? w?.paths?.[0] ?? "",
      });
    },
    openPrompt: (title, value, onOk) => { setDlgInput(value); setDlg({ mode: "prompt", title, onOk }); },
    openConfirm: (title, onOk) => setDlg({ mode: "confirm", title, onOk }),
    removeWorkspace: (wsId, label) => api.openConfirm(t("sun.delete-ws-q", { label }), () => {
      void service.deleteWorkspace({ sessionId: ctx.sessionId, workspaceId: wsId })
        .then(actions.onChanged).catch(ctx.reportError);
    }),
  };

  const node = (
    <>
      {browse && (
        <div className="omni-dlg-mask" onClick={() => setBrowse(null)}>
          <div className="omni-dlg omni-browse" onClick={(e) => e.stopPropagation()}>
            <p className="omni-dlg-title">{t("sun.pick-dir")}</p>
            <div className="browse-path" title={browse.path}>{browse.path}</div>
            <div className="browse-list">
              {browse.parent !== browse.path && (
                <button className="browse-row" onClick={() => api.openBrowse(browse.parent)} type="button">..</button>
              )}
              {browse.dirs.map((d) => (
                <button className="browse-row" key={d} onClick={() => api.openBrowse(browse.path + "/" + d.slice(0, -1))} type="button">📁 {d}</button>
              ))}
              {browse.dirs.length === 0 && <span className="browse-empty">{t("sun.no-subdir")}</span>}
            </div>
            <div className="omni-dlg-btns">
              <button className="omni-dlg-cancel" onClick={() => setBrowse(null)} type="button">{t("common.cancel")}</button>
              <button className="omni-dlg-ok" onClick={() => {
                void service.upsertWorkspace({
                  sessionId: ctx.sessionId, workspaceId: browse.path, displayName: browse.path.split("/").filter(Boolean).pop() ?? browse.path, path: browse.path,
                }).then(() => { actions.setFolder(sessionId, browse.path); setBrowse(null); });
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
                const { wsId } = editWs;
                const label = editWs.name || editWs.wsId;
                setEditWs(null);
                api.removeWorkspace(wsId, label);
              }} type="button">{t("sun.remove-local")}</button>
              <button className="omni-dlg-cancel" onClick={() => setEditWs(null)} type="button">{t("common.cancel")}</button>
              <button className="omni-dlg-ok" onClick={() => {
                void service.upsertWorkspace({
                  sessionId: ctx.sessionId, workspaceId: editWs.wsId, displayName: editWs.name.trim(),
                  path: editWs.primary, paths: editWs.paths,
                }).then(() => { actions.onChanged(); setEditWs(null); });
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
    </>
  );
  return { api, node };
}
