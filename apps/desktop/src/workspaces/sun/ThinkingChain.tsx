import type { RunEventView } from "../../types";

/** Thinking chain — folds durable run events (stage/task/approval lifecycle)
 *  into an ordered step timeline. Real data only: no invented reasoning. */
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
      case "task_started":
        at("t", `${String(p.verb ?? "task")} · ${String(p.executor ?? "")}`, String(p.task_id ?? ""));
        open.set(`task:${p.task_id}`, steps.length - 1);
        break;
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

export function ThinkingChain({ events }: { events: RunEventView[] | undefined }) {
  const steps = thinkingSteps(events);
  // model reply stream — assistant_delta carries the LLM's visible output
  // tokens; fold them into a preview inside the chain.
  const reply = (events ?? [])
    .filter((e) => (e.eventType ?? e.type) === "assistant_delta")
    .map((e) => String((e.payload as Record<string, unknown>)?.delta ?? ""))
    .join("");
  if (steps.length === 0 && !reply) return null;
  const doneCount = steps.filter((s) => s.done).length;
  return (
    <details className="msg3 thinking" open={steps.some((s) => !s.done)}>
      <summary>
        <span className="think-glyph">◈</span> 思考过程
        <em>{steps.length ? `${doneCount}/${steps.length}` : ""}{reply ? " · 回复流" : ""}</em>
      </summary>
      <div className="think-list">
        {steps.map((s) => (
          <div className={`think-step ${s.done ? "done" : "live"}`} key={s.key}>
            <span className="think-dot">{s.done ? "✓" : "◐"}</span>
            <span className="think-label">{s.label}</span>
            {s.detail && <span className="think-detail">{s.detail}</span>}
          </div>
        ))}
        {reply && (
          <div className="think-reply">
            <span className="think-detail">assistant_delta</span>
            <p>{reply.slice(-600)}</p>
          </div>
        )}
      </div>
    </details>
  );
}
