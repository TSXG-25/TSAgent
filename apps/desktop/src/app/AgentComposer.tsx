import { useEffect, useRef, useState, type FormEvent } from "react";
import { submitToPlanet } from "./delegate";
import { selectWorkspaceRun, useWorkspaceCurrentRun } from "./workspaceSelection";
import { Icon } from "./ui";
import type { RunCtx } from "./runCtx";
import { draftKey, useDraft, clearDraftIfUnchanged, useDraftFocus } from "./draftStore";
import { rememberRunAttachments } from "./runAttachments";
import { t } from "./i18n";

const BUSY = new Set(["active", "pending"]);

/** Shared AgentComposer — "same as Omni input". Planet-bound task dispatch
 *  via submitToPlanet. Supports @planet delegation and /slash shortcuts
 *  (/mars x → @mars x, /model <m> → local hint, /help). The model pick rides
 *  the run's metadata.agent_profile.model_ref — a real per-run override.
 *  Drafts live in the app-level draft store keyed by tenant/session/target so
 *  GlobalChat and workspace composers share one draft; cleared only after the
 *  request is accepted. */
const PLANETS = ["sun","mercury","venus","earth","mars","jupiter","saturn","uranus","neptune","pluto"];
const DEEPSEEK_MODELS = ["deepseek-chat", "deepseek-reasoner"];

export default function AgentComposer({ ctx, planet, placeholder, contextPrefix, onSend }: { ctx: RunCtx; planet?: string; placeholder?: string; contextPrefix?: string; onSend?: (to: string, body: string, files: string[], options: { model?: string }) => Promise<{ runId: string; sessionId: string }> }) {
  const target = planet ?? "sun";
  const key = draftKey(ctx.service.identity.tenantId, ctx.sessionId, target);
  const [draft, setDraft] = useDraft(key);
  const text = draft.text;
  const files = draft.files;
  const setText = (v: string | ((x: string) => string)) => setDraft({ ...draft, text: typeof v === "function" ? v(draft.text) : v });
  const setFiles = (v: string[] | ((x: string[]) => string[])) => setDraft({ ...draft, files: typeof v === "function" ? v(draft.files) : v });
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [wsFiles, setWsFiles] = useState<{ path: string }[] | null>(null);
  const [wsError, setWsError] = useState<string | null>(null);
  const [wsFilter, setWsFilter] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const composingRef = useRef(false);
  // Landing chips / search results prefill this draft — never submit it.
  useDraftFocus(key, textareaRef);
  const refLabel = (ref: string) => ref.split(":").slice(1).join(":").split("/").pop() ?? ref;
  const toggleRef = (ref: string) => setFiles((x) => x.includes(ref) ? x.filter((f) => f !== ref) : [...x, ref]);
  const openPicker = () => {
    setPickerOpen((o) => !o);
    if (pickerOpen) return;
    setWsError(null); setWsFiles(null);
    void ctx.service.listWorkspaceFiles({
      sessionId: ctx.sessionId,
      }).then(setWsFiles).catch((e) => setWsError(String(e instanceof Error ? e.message : e)));
  };
  const pickFromFinder = () => {
    const pick = ("__TAURI_INTERNALS__" in window)
      ? import("@tauri-apps/plugin-dialog").then(({ open }) =>
          open({ multiple: true, title: t("composer.attach") }).then((r) => (r === null ? [] : Array.isArray(r) ? r : [r])))
      : ctx.service.pickFiles().then((r) => r.paths);
    void pick
      .then((paths) => paths.length === 0 ? { contextRefs: [] as string[] } : ctx.service.attachFiles({
        sessionId: ctx.sessionId,
        paths,
      }))
      .then((r) => {
        if (r.contextRefs.length === 0) return;
        setFiles((x) => [...x, ...r.contextRefs.filter((ref) => !x.includes(ref))]);
        setPickerOpen(false);
      })
      .catch((e) => setWsError(String(e instanceof Error ? e.message : e)));
  };
  // context usage ring — ≈tokens from visible conversation text, cap 1M
  const estTokens = Math.round(((ctx.activeRun?.conversation ?? []).reduce((n, m) => n + m.content.length, 0)
    + (ctx.activeRun?.output?.text.length ?? 0) + (ctx.activeRun?.request?.length ?? 0)) / 4);
  const pct = Math.min(estTokens / 1000000, 1);
  const R = 9, C = 2 * Math.PI * R;
  const [model, setModel] = useState("default");
  // Model choices come from the runtime's provider settings — an ollama
  // deployment cannot run deepseek-* names, and vice versa.
  const [models, setModels] = useState<string[]>(DEEPSEEK_MODELS);
  useEffect(() => {
    void ctx.service.getSettings({ sessionId: ctx.sessionId }).then((s) => {
      setModels(s.providerMode === "local" && s.ollamaModel ? [s.ollamaModel] : DEEPSEEK_MODELS);
    }).catch(() => {});
  }, [ctx.service, ctx.sessionId]);
  const [modelOpen, setModelOpen] = useState(false);
  // Workstation composers control the run selected on *this* planet's
  // rail — never the session-global activeRun, which may belong to
  // another workstation's task. The sun composer keeps the global view.
  const scopedRun = useWorkspaceCurrentRun(ctx, target);
  const runHere = planet
    ? (scopedRun && scopedRun.sessionId === ctx.sessionId ? scopedRun : null)
    : (ctx.activeRun && ctx.activeRun.sessionId === ctx.sessionId ? ctx.activeRun : null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const body0 = text.trim();
    if (!body0 || busy) return;
    // /planet shortcut
    const slash = body0.match(/^\/(\w+)\s+([\s\S]+)$/);
    const m = slash ?? body0.match(/^@(\w+)\s+([\s\S]+)$/);
    if (slash && !PLANETS.includes(slash[1].toLowerCase())) {
      setNotice(`${t("composer.unknown-command")} /${slash[1]} — ${PLANETS.map((p) => `/${p}`).join(" ")}`);
      return;
    }
    setBusy(true); setNotice(null);
    const submitted = { text, files };
    try {
      const to = m ? m[1].toLowerCase() : target;
      const body = contextPrefix ? `${contextPrefix}\n${m ? m[2] : body0}` : (m ? m[2] : body0);
      const modelOptions = model !== "default" ? { model } : {};
      const sent = onSend
        ? await onSend(to, body, files, modelOptions)
        : { runId: await submitToPlanet(ctx, to, body, files, modelOptions), sessionId: ctx.sessionId };
      // The workstation the send targeted now owns this task — its rail
      // selects it without navigating away from where the user is.
      selectWorkspaceRun(ctx.service.identity.tenantId, sent.sessionId, to, sent.runId);
      if (files.length) rememberRunAttachments(sent.runId, files);
      // Only the submitted-and-unmodified draft is cleared — anything typed
      // while the request was in flight stays.
      clearDraftIfUnchanged(key, submitted);
      setNotice(t("composer.delegated", { target: to }));
    } catch (err) {
      // failed submits keep the draft — it lives in the store, untouched here
      setNotice(`${t("composer.submit-failed")}: ${String(err instanceof Error ? err.message : err)}`);
    }
    finally { setBusy(false); }
  }

  return (
    <form className="agent-composer" onSubmit={submit}>
      <textarea
        ref={textareaRef}
        onChange={(e) => setText(e.target.value)}
        onCompositionStart={() => { composingRef.current = true; }}
        onCompositionEnd={() => { composingRef.current = false; }}
        onKeyDown={(e) => {
          if (e.key !== "Enter" || e.shiftKey) return;
          if (e.nativeEvent.isComposing || composingRef.current) return;
          e.preventDefault(); void submit(e);
        }}
        placeholder={placeholder ?? t("composer.placeholder")}
        rows={2} value={text}
      />
      {files.length > 0 && (
        <div className="ac-chips">
          {files.map((f, i) => (
            <span className="ac-chip" key={i} title={f}>
              {refLabel(f)}
              <small className="ac-chip-type">{f.split(".").pop()?.toUpperCase()}</small>
              <i onClick={() => setFiles((x) => x.filter((_, j) => j !== i))}>✕</i>
            </span>
          ))}
        </div>
      )}
      <div className="ac-tools">
        <div className="ac-attachwrap">
          <button aria-label={t("composer.attach")} className="ac-attach" onClick={openPicker} title={t("composer.attach-title")} type="button">＋</button>
          {pickerOpen && (
            <div className="ac-filepicker">
              <button className="ac-fp-row" onClick={pickFromFinder} type="button">
                <span>{t("composer.from-finder")}</span>
              </button>
              <input autoFocus className="ac-fp-filter" onChange={(e) => setWsFilter(e.target.value)} placeholder={t("composer.filter")} value={wsFilter} />
              {wsError && <p className="omni-empty">{wsError}</p>}
              {!wsError && wsFiles === null && <p className="omni-empty">…</p>}
              {(wsFiles ?? []).filter((f) => !wsFilter || f.path.toLowerCase().includes(wsFilter.toLowerCase())).map((f) => {
                const ref = `file:${f.path}`;
                return (
                  <button className={`ac-fp-row ${files.includes(ref) ? "on" : ""}`} key={f.path} onClick={() => toggleRef(ref)} type="button">
                    <span>{f.path}</span>{files.includes(ref) && <i>✓</i>}
                  </button>
                );
              })}
              {wsFiles !== null && wsFiles.length === 0 && !wsError && <p className="omni-empty">{t("composer.workspace-empty")}</p>}
            </div>
          )}
        </div>
        <span className="ac-planet">@{target}</span>
        {runHere?.status === "blocked" && (
          <span className="ac-approval" role="status">{t("composer.waiting-approval")}</span>
        )}
        <span className="ac-spacer" />
        <span className="ac-ctxring" title={`Context usage ≈${estTokens.toLocaleString()} / 1M tokens (est.)`}>
          <svg height="22" viewBox="0 0 22 22" width="22">
            <circle cx="11" cy="11" fill="none" r={R} stroke="rgba(255,255,255,.1)" strokeWidth="2" />
            <circle cx="11" cy="11" fill="none" r={R} stroke="var(--planet-accent)" strokeDasharray={`${pct * C} ${C}`} strokeLinecap="round" strokeWidth="2" transform="rotate(-90 11 11)" />
          </svg>
          <em>{Math.round(pct * 100)}%</em>
        </span>
        <div className="ac-modelwrap">
          <button className="ac-modeltrig" onClick={() => setModelOpen((o) => !o)} type="button">
            {model === "default" ? t("composer.model") : model} ▾
          </button>
          {modelOpen && (
            <div className="ac-modelmenu">
              {["default", ...models].map((m) => (
                <button className={`ac-mrow ${model === m ? "on" : ""}`} key={m} onClick={() => { setModel(m); setModelOpen(false); }} type="button">
                  <span>{m === "default" ? t("composer.model-default") : m}</span>{model === m && <i>✓</i>}
                </button>
              ))}
            </div>
          )}
        </div>
        {runHere && BUSY.has(runHere.status) && (
          <>
          <button aria-label={t("composer.pause")} className="ac-send ac-pause" onClick={() => { void ctx.controller.pauseRun(runHere.runId).catch(() => {}); }} title={t("composer.pause")} type="button">❚❚</button>
          <button aria-label={t("composer.stop")} className="ac-send ac-stop" onClick={() => { void ctx.controller.cancelRun(runHere.runId).catch(() => {}); }} title={t("composer.stop")} type="button">
            <Icon name="x" size={12} />
          </button>
          </>
        )}
        {runHere?.status === "cancelling" && (
          <button className="ac-send ac-stop" disabled title={t("composer.cancelling")} type="button">
            <Icon name="x" size={12} />
          </button>
        )}
        <button aria-label={t("composer.send-enter")} className="ac-send" disabled={busy || !text.trim()} title={t("composer.send-enter")} type="submit">
          <Icon name="send" size={14} />
        </button>
      </div>
      {text.endsWith("@") && (
        <div className="ac-atmenu">
          <button className="ac-at-item" onClick={() => { setText((x) => x.slice(0, -1)); openPicker(); }} type="button">📎 {t("composer.attach")}</button>
          {PLANETS.map((p) => (
            <button className="ac-at-item" key={p} onClick={() => setText((x) => x + p + " ")} type="button">@{p}</button>
          ))}
        </div>
      )}
      {text.startsWith("/") && (
        <div className="ac-slash">
          {PLANETS.filter((p) => p.startsWith(text.slice(1).split(" ")[0].toLowerCase())).slice(0, 6).map((p) => (
            <button className="ac-slash-item" key={p} onClick={() => setText(`/${p} `)} type="button">/{p}</button>
          ))}
        </div>
      )}
      {notice && <span className="ac-notice">{notice}</span>}
    </form>
  );
}
