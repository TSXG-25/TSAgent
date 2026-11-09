import type { ConversationMessageView, RunEventView } from "../../types";

/** Thinking chain — folds durable run events and intermediate assistant
 *  narration into work segments: each stage becomes a "Worked for Ns"
 *  collapsible containing Thoughts blocks (model narration) and Ran lines
 *  (tool invocations). The reply stream and final output are NOT here —
 *  they render as regular message bubbles. Real data only: no invented
 *  reasoning. */

interface RanItem { kind: "ran"; key: string; label: string; detail: string; done: boolean }
interface ThoughtItem { kind: "thought"; key: string; role: string; text: string }
interface RowItem { kind: "row"; key: string; label: string; done: boolean }
type SegItem = RanItem | ThoughtItem | RowItem;

interface Seg {
  kind: "seg";
  key: string;
  phase: string;
  seconds: number | null;
  live: boolean;
  items: SegItem[];
}

type Item = Seg | SegItem;

function secsBetween(start: string | undefined, end: string | undefined): number | null {
  if (!start || !end) return null;
  const ms = Date.parse(end) - Date.parse(start);
  return Number.isFinite(ms) && ms >= 0 ? Math.round(ms / 100) / 10 : null;
}

function buildItems(events: RunEventView[] | undefined, messages: ConversationMessageView[]): Item[] {
  const items: Item[] = [];
  const openSegs = new Map<string, Seg>();
  const openRuns = new Map<string, RanItem>();
  let cur: Seg | null = null;
  const sink = (item: SegItem) => (cur ? cur.items.push(item) : items.push(item));

  const timeline = [
    ...(events ?? []).map((e) => ({ kind: "ev" as const, at: e.timestamp ?? "", seq: e.sequenceNumber ?? 0, ev: e })),
    ...messages.map((m) => ({ kind: "msg" as const, at: m.at ?? "", seq: Number.MAX_SAFE_INTEGER - 1, msg: m })),
  ].sort((a, b) => (a.at === b.at ? a.seq - b.seq : a.at < b.at ? -1 : 1));

  for (const entry of timeline) {
    if (entry.kind === "msg") {
      sink({ kind: "thought", key: entry.msg.messageId, role: entry.msg.role, text: entry.msg.content });
      continue;
    }
    const e = entry.ev;
    const et = e.eventType ?? e.type;
    const p = (e.payload ?? {}) as Record<string, unknown>;
    switch (et) {
      case "run_created":
        sink({ kind: "row", key: e.eventId, label: "任务创建", done: true });
        break;
      case "run_started":
        sink({ kind: "row", key: e.eventId, label: "开始执行", done: true });
        break;
      case "run_completed":
        sink({ kind: "row", key: e.eventId, label: "完成", done: true });
        break;
      case "run_failed":
        sink({ kind: "row", key: e.eventId, label: "失败", done: true });
        break;
      case "run_cancelled":
      case "run_cancelling":
        sink({ kind: "row", key: e.eventId, label: "已取消", done: true });
        break;
      case "stage_started": {
        const seg: Seg = {
          kind: "seg",
          key: e.eventId,
          phase: String(p.phase ?? p.stage_id ?? "stage"),
          seconds: null,
          live: true,
          items: [],
        };
        items.push(seg);
        openSegs.set(String(p.stage_id ?? p.phase ?? ""), seg);
        cur = seg;
        break;
      }
      case "stage_completed": {
        const seg = openSegs.get(String(p.stage_id ?? p.phase ?? ""));
        if (seg) {
          seg.live = false;
          const started = (events ?? []).find(
            (x) =>
              (x.eventType ?? x.type) === "stage_started" &&
              String((x.payload ?? {}).stage_id ?? (x.payload ?? {}).phase ?? "") ===
                String(p.stage_id ?? p.phase ?? ""),
          );
          seg.seconds = secsBetween(started?.timestamp, e.timestamp);
          openSegs.delete(String(p.stage_id ?? p.phase ?? ""));
        }
        cur = null;
        break;
      }
      case "task_started": {
        const target = String(p.target ?? "");
        const tool = String(p.tool ?? "");
        const ran: RanItem = {
          kind: "ran",
          key: e.eventId,
          label: `Ran ${tool || String(p.verb ?? "task")}${target ? ` ${target}` : ""}`,
          detail: String(p.task_id ?? ""),
          done: false,
        };
        sink(ran);
        openRuns.set(String(p.task_id), ran);
        break;
      }
      case "task_completed": {
        const ran = openRuns.get(String(p.task_id));
        if (ran) ran.done = true;
        break;
      }
      case "approval.requested":
        sink({ kind: "row", key: e.eventId, label: `审批 · ${String(p.action ?? p.risk_level ?? "")}`, done: false });
        break;
      case "approval.decided":
        sink({ kind: "row", key: e.eventId, label: `审批已决定 · ${String(p.decision ?? "")}`, done: true });
        break;
      case "run_waiting_user":
      case "run_blocked":
        sink({ kind: "row", key: e.eventId, label: "等待用户", done: false });
        break;
    }
  }
  return items;
}

function countSteps(items: Item[]): number {
  let n = 0;
  for (const item of items) {
    if (item.kind === "seg") n += item.items.length;
    else n += 1;
  }
  return n;
}

function SegView({ seg }: { seg: Seg }) {
  const title = seg.live ? `Working… · ${seg.phase}` : `Worked for ${seg.seconds ?? "?"}s · ${seg.phase}`;
  return (
    <details className={`think-seg ${seg.live ? "live" : ""}`} open={seg.live}>
      <summary>{title}</summary>
      <div className="think-seg-body">
        {seg.items.map((item) => (
          <ItemView item={item} key={item.key} />
        ))}
      </div>
    </details>
  );
}

function ItemView({ item }: { item: SegItem }) {
  if (item.kind === "thought") {
    return (
      <details className="think-thought">
        <summary>Thoughts</summary>
        <p>{item.text}</p>
      </details>
    );
  }
  if (item.kind === "ran") {
    return (
      <div className={`think-ran ${item.done ? "done" : "live"}`}>
        <span className="think-dot">{item.done ? "✓" : "◐"}</span>
        <span className="think-cmd">{item.label}</span>
      </div>
    );
  }
  return (
    <div className={`think-row ${item.done ? "done" : "live"}`}>
      <span className="think-dot">{item.done ? "✓" : "◐"}</span>
      <span>{item.label}</span>
    </div>
  );
}

export function ThinkingChain({
  events,
  messages = [],
}: {
  events: RunEventView[] | undefined;
  messages?: ConversationMessageView[];
}) {
  const items = buildItems(events, messages);
  if (items.length === 0) return null;
  const live = items.some((i) => (i.kind === "seg" ? i.live : i.kind !== "thought" && !i.done));
  const total = countSteps(items);
  return (
    <details className="msg3 thinking" open={live}>
      <summary>
        <span className="think-glyph">◈</span> 思考过程
        <em>{total ? `${total} 条` : ""}</em>
      </summary>
      <div className="think-list">
        {items.map((item) =>
          item.kind === "seg" ? (
            <SegView seg={item} key={item.key} />
          ) : item.kind === "thought" ? (
            <ItemView item={item} key={item.key} />
          ) : (
            <ItemView item={item} key={item.key} />
          ),
        )}
      </div>
    </details>
  );
}
