import type { ConversationMessageView, RunEventView } from "../../types";

/** Thinking chain — folds durable run events (stage/task/approval lifecycle)
 *  and intermediate assistant narration into an ordered step timeline inside
 *  one collapsible box. The reply stream and final output are NOT here — they
 *  render as regular message bubbles. Real data only: no invented reasoning. */
interface Step { key: string; label: string; detail: string; done: boolean; seq: number }

export function thinkingSteps(events: RunEventView[] | undefined): Step[] {
  const steps: Step[] = [];
  const open = new Map<string, number>();
  for (const e of events ?? []) {
    const et = e.eventType ?? e.type;
    const p = (e.payload ?? {}) as Record<string, unknown>;
    const at = (key: string, label: string, detail: string) =>
      steps.push({ key: `${e.eventId}`, label, detail, done: false, seq: e.sequenceNumber ?? 0 });
    switch (et) {
      case "run_created": at("r", "任务创建", ""); steps[steps.length-1].done = true; break;
      case "run_started": at("r", "开始执行", ""); steps[steps.length-1].done = true; break;
      case "run_completed": at("r", "完成", ""); steps[steps.length-1].done = true; break;
      case "run_failed": at("r", "失败", ""); steps[steps.length-1].done = true; break;
      case "run_cancelled": case "run_cancelling": at("r", "已取消", ""); steps[steps.length-1].done = true; break;
      case "stage_started":
        at("s", `阶段 · ${String(p.phase ?? p.stage_id ?? "")}`, "");
        open.set(`stage:${String(p.stage_id ?? p.phase ?? "")}`, steps.length - 1);
        break;
      case "stage_completed": {
        const i = open.get(`stage:${String(p.stage_id ?? p.phase ?? "")}`);
        if (i !== undefined) steps[i].done = true;
        break;
      }
      case "task_started": {
        const target = String(p.target ?? "");
        const tool = String(p.tool ?? "");
        at("t",
          `${String(p.verb ?? "task")}${target ? ` ${target}` : ""}`,
          [tool, String(p.task_id ?? "")].filter(Boolean).join(" · "));
        open.set(`task:${p.task_id}`, steps.length - 1);
        break;
      }
      case "task_completed": {
        const i = open.get(`task:${p.task_id}`);
        if (i !== undefined) steps[i].done = true;
        break;
      }
      case "approval.requested":
        at("a", `审批 · ${String(p.action ?? p.risk_level ?? "")}`, String(p.approval_id ?? ""));
        open.set(`ap:${p.approval_id}`, steps.length - 1);
        break;
      case "approval.decided": {
        const i = open.get(`ap:${p.approval_id}`);
        if (i !== undefined) steps[i].done = true;
        break;
      }
      case "run_waiting_user":
      case "run_blocked":
        at("w", "等待用户", "");
        break;
    }
  }
  return steps;
}

export function ThinkingChain({
  events,
  messages = [],
}: {
  events: RunEventView[] | undefined;
  messages?: ConversationMessageView[];
}) {
  const steps = thinkingSteps(events);
  if (steps.length === 0 && messages.length === 0) return null;
  const doneCount = steps.filter((s) => s.done).length;
  const live = steps.some((s) => !s.done);
  return (
    <details className="msg3 thinking" open={live}>
      <summary>
        <span className="think-glyph">◈</span> 思考过程
        <em>{steps.length ? `${doneCount}/${steps.length}` : ""}{messages.length ? ` · ${messages.length} 条记录` : ""}</em>
      </summary>
      <div className="think-list">
        {steps.map((s) => (
          <div className={`think-step ${s.done ? "done" : "live"}`} key={s.key}>
            <span className="think-dot">{s.done ? "✓" : "◐"}</span>
            <span className="think-label">{s.label}</span>
            {s.detail && <span className="think-detail">{s.detail}</span>}
          </div>
        ))}
        {messages.map((m) => (
          <div className="think-msg" key={m.messageId}>
            <span className="think-detail">{m.role === "system" ? "system" : "assistant"}</span>
            <p>{m.content}</p>
          </div>
        ))}
      </div>
    </details>
  );
}
