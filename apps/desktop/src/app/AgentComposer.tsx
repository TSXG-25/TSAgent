import { useEffect, useState, type FormEvent } from "react";
import { delegateToPlanet } from "./delegate";
import { Icon } from "./ui";
import type { RunCtx } from "./runCtx";

/** Shared AgentComposer — "same as Omni input" across all 10 workspaces.
 *  Planet-bound task dispatch via the canonical delegateToPlanet path.
 *  Layout width/position varies per workspace; behavior is identical. */
export default function AgentComposer({ ctx, planet, placeholder }: { ctx: RunCtx; planet?: string; placeholder?: string }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [settings, setSettings] = useState<{ active_model?: string; active_provider?: string; deepseek_model?: string; ollama_model?: string } | null>(null);

  useEffect(() => {
    void ctx.service.getSettings({ tenantId: ctx.service.identity.tenantId, sessionId: ctx.service.identity.sessionId, requestId: `cfg-${Date.now()}` } as never)
      .then((s) => setSettings(s as never)).catch(() => {});
  }, [ctx.service]);

  async function pickModel(model: string) {
    try {
      const field = model.includes(":") ? { ollamaModel: model } : { deepseekModel: model };
      await ctx.service.updateSettings({ tenantId: ctx.service.identity.tenantId, sessionId: ctx.service.identity.sessionId, requestId: `cfg-up-${Date.now()}`, ...field } as never);
      setSettings((s) => s && { ...s, [model.includes(":") ? "ollama_model" : "deepseek_model"]: model });
    } catch (e) { setNotice(String(e)); }
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    const t = text.trim();
    if (!t || busy) return;
    setBusy(true); setNotice(null);
    // slash commands — session/run control from the composer
    if (t.startsWith("/")) {
      const cmd = t.slice(1).toLowerCase();
      try {
        if (cmd === "new") { window.dispatchEvent(new CustomEvent("tsagent:new-session")); setNotice("新任务已建"); }
        else if (cmd === "pause") { if (ctx.activeRun) await ctx.service.pauseRun({ tenantId: ctx.service.identity.tenantId, sessionId: ctx.service.identity.sessionId, runId: ctx.activeRun.runId, requestId: `pause-${Date.now()}` } as never); setNotice("已暂停"); }
        else if (cmd === "resume") { if (ctx.activeRun) await ctx.controller.resumeRun(ctx.activeRun.runId); setNotice("已恢复"); }
        else if (cmd === "cancel") { if (ctx.activeRun) await ctx.controller.cancelRun(ctx.activeRun.runId); setNotice("已取消"); }
        else setNotice("命令: /new /pause /resume /cancel");
      } catch (err) { setNotice(String(err)); }
      finally { setBusy(false); }
      setText(""); return;
    }
    try {
      const m = t.match(/^@(\w+)\s+([\s\S]+)$/);
      const target = m ? m[1].toLowerCase() : (planet ?? "sun");
      await delegateToPlanet(ctx, target, m ? m[2] : t);
      setText(""); setNotice(`→ ${target} task started`);
    } catch (err) { setNotice(String(err)); }
    finally { setBusy(false); }
  }

  return (
    <form className="agent-composer" onSubmit={submit}>
      <span className="ac-planet">@{planet ?? "sun"}</span>
      <textarea
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void submit(e); } }}
        placeholder={placeholder ?? `Task for ${planet ?? "omni"} — @planet delegates…`}
        rows={2} value={text}
      />
      <select className="omni-model" onChange={(e) => void pickModel(e.target.value)} title="Model" value={settings?.active_provider === "ollama" ? settings?.ollama_model : settings?.deepseek_model}>
        {[settings?.deepseek_model, settings?.ollama_model].filter(Boolean).map((m) => <option key={m} value={m}>{m}</option>)}
        {!settings && <option>…</option>}
      </select>
      <button className="ac-send" disabled={busy || !text.trim()} type="submit">
        <Icon name="send" size={14} />
      </button>
      {notice && <span className="ac-notice">{notice}</span>}
    </form>
  );
}
