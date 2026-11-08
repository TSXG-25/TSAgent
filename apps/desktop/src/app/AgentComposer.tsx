import { useState, type FormEvent } from "react";
import { delegateToPlanet } from "./delegate";
import { pickFiles } from "../service/tauriBridge";
import { Icon } from "./ui";
import type { RunCtx } from "./runCtx";

const BUSY = new Set(["active", "pending"]);

/** Shared AgentComposer — "same as Omni input". Planet-bound task dispatch
 *  via delegateToPlanet. Supports @planet delegation and /slash shortcuts
 *  (/mars x → @mars x, /model <m> → local hint, /help). Model select is a
 *  display+hint surface until a model-setting wire exists per-run. */
const PLANETS = ["sun","mercury","venus","earth","mars","jupiter","saturn","uranus","neptune","pluto"];
const MODELS = ["deepseek-chat", "deepseek-reasoner"];
const EFFORTS = ["默认", "low", "medium", "high"];
const PERMISSIONS = [
  { id: "Read-only", icon: "◧", desc: "Answer only, no file changes" },
  { id: "Ask", icon: "◈", desc: "Confirm each write operation" },
  { id: "Smart", icon: "✦", desc: "Auto-approve low-risk actions" },
  { id: "Plan", icon: "▤", desc: "Propose plan before executing" },
  { id: "Bypass", icon: "⏩", desc: "Auto-approve everything (local pref)" },
] as const;

export default function AgentComposer({ ctx, planet, placeholder }: { ctx: RunCtx; planet?: string; placeholder?: string }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [files, setFiles] = useState<string[]>([]);
  const [perm, setPerm] = useState(() => localStorage.getItem("tsagent:permission") ?? "Ask");
  const [permOpen, setPermOpen] = useState(false);
  const pickPerm = (id: string) => { setPerm(id); localStorage.setItem("tsagent:permission", id); setPermOpen(false); };
  // context usage ring — ≈tokens from visible conversation text, cap 1M
  const estTokens = Math.round(((ctx.activeRun?.conversation ?? []).reduce((n, m) => n + m.content.length, 0)
    + (ctx.activeRun?.output?.text.length ?? 0) + (ctx.activeRun?.request?.length ?? 0)) / 4);
  const pct = Math.min(estTokens / 1000000, 1);
  const R = 9, C = 2 * Math.PI * R;
  const [model, setModel] = useState("default");
  const [effort, setEffort] = useState("默认");
  const [modelOpen, setModelOpen] = useState(false);
  const [pane, setPane] = useState<"root" | "model" | "effort">("root");

  async function submit(e: FormEvent) {
    e.preventDefault();
    const t = text.trim();
    if (!t || busy) return;
    // /planet shortcut
    const slash = t.match(/^\/(\w+)\s+([\s\S]+)$/);
    const m = slash ?? t.match(/^@(\w+)\s+([\s\S]+)$/);
    if (slash && !PLANETS.includes(slash[1].toLowerCase())) {
      setNotice(`未知命令 /${slash[1]} — 可用: ${PLANETS.map((p) => `/${p}`).join(" ")}`);
      return;
    }
    setBusy(true); setNotice(null);
    try {
      const target = m ? m[1].toLowerCase() : (planet ?? "sun");
      await delegateToPlanet(ctx, target, m ? m[2] : t, files, {
        ...(model !== "default" || effort !== "默认" ? { model: model === "default" ? undefined : model, effort: effort === "默认" ? undefined : effort } : {}),
      });
      if (files.length && ctx.activeRun) {
        window.dispatchEvent(new CustomEvent("tsagent:run-attachments", { detail: { runId: ctx.activeRun.runId, files } }));
      }
      setText(""); setFiles([]); setNotice(`→ ${target}`);
    } catch (err) { setNotice(String(err)); }
    finally { setBusy(false); }
  }

  return (
    <form className="agent-composer" onSubmit={submit}>
      <textarea
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void submit(e); } }}
        placeholder={placeholder ?? `Task for ${planet ?? "omni"} — @planet 或 /planet 委派…`}
        rows={2} value={text}
      />
      {files.length > 0 && (
        <div className="ac-chips">
          {files.map((f, i) => (
            <span className="ac-chip" key={i} title={f}>
              {f.split("/").pop()}
              <i onClick={() => setFiles((x) => x.filter((_, j) => j !== i))}>✕</i>
            </span>
          ))}
        </div>
      )}
      <div className="ac-tools">
        <button className="ac-attach" onClick={() => void pickFiles().then((f) => setFiles((x) => [...x, ...f]))} title="Add files" type="button">＋</button>
        <div className="ac-permwrap">
          <button className="ac-perm" onClick={() => setPermOpen((o) => !o)} title="Permission preset (local preference)" type="button">{perm} ▾</button>
          {permOpen && (
            <div className="ac-permmenu">
              {PERMISSIONS.map((p) => (
                <button className={`ac-perm-item ${perm === p.id ? "on" : ""}`} key={p.id} onClick={() => pickPerm(p.id)} type="button">
                  <span className="pi">{p.icon}</span>
                  <span className="pt"><strong>{p.id}</strong><em>{p.desc}</em></span>
                  {perm === p.id && <span className="pc">✓</span>}
                </button>
              ))}
            </div>
          )}
        </div>
        <span className="ac-planet">@{planet ?? "sun"}</span>
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
            {model === "default" ? "Select model" : model} <em>{effort}</em> ▾
          </button>
          {modelOpen && (
            <div className="ac-modelmenu">
              {pane === "root" ? (
                <>
                  <button className="ac-mrow" onClick={() => setPane("model")} type="button">
                    <span>Model</span><em>{model === "default" ? "—" : model}</em><i>›</i>
                  </button>
                  <button className="ac-mrow" onClick={() => setPane("effort")} type="button">
                    <span>Effort</span><em>{effort}</em><i>›</i>
                  </button>
                </>
              ) : pane === "model" ? (
                <>
                  <button className="ac-mrow back" onClick={() => setPane("root")} type="button">‹ Model</button>
                  {["default", ...MODELS].map((m) => (
                    <button className={`ac-mrow ${model === m ? "on" : ""}`} key={m} onClick={() => { setModel(m); setPane("root"); setModelOpen(false); }} type="button">
                      <span>{m === "default" ? "默认模型" : m}</span>{model === m && <i>✓</i>}
                    </button>
                  ))}
                </>
              ) : (
                <>
                  <button className="ac-mrow back" onClick={() => setPane("root")} type="button">‹ Effort</button>
                  {EFFORTS.map((e) => (
                    <button className={`ac-mrow ${effort === e ? "on" : ""}`} key={e} onClick={() => { setEffort(e); setPane("root"); setModelOpen(false); }} type="button">
                      <span>{e}</span>{effort === e && <i>✓</i>}
                    </button>
                  ))}
                </>
              )}
            </div>
          )}
        </div>
        {ctx.activeRun && ctx.activeRun.sessionId === ctx.sessionId && BUSY.has(ctx.activeRun.status) ? (
          <button className="ac-send ac-stop" onClick={() => { void ctx.controller.cancelRun(ctx.activeRun!.runId).catch(() => {}); }} title="停止" type="button">
            <Icon name="x" size={12} />
          </button>
        ) : (
          <button className="ac-send" disabled={busy || !text.trim()} type="submit">
            <Icon name="send" size={14} />
          </button>
        )}
      </div>
      {text.endsWith("@") && (
        <div className="ac-atmenu">
          <button className="ac-at-item" onClick={() => { setText((t) => t.slice(0, -1)); void pickFiles().then((f) => setFiles((x) => [...x, ...f])); }} type="button">📎 文件</button>
          {PLANETS.map((p) => (
            <button className="ac-at-item" key={p} onClick={() => setText((t) => t + p + " ")} type="button">@{p}</button>
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
