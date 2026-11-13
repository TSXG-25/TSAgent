import type { ConversationMessageView, RunEventView } from "../../types";
import { t } from "../../app/i18n";

/** Thinking chain — folds durable run events and intermediate assistant
 *  narration into work segments: each stage becomes a collapsible containing
 *  Thoughts blocks (model narration) and tool cards (invocations). The reply
 *  stream and final output are NOT here — they render as regular message
 *  bubbles. Real data only: no invented reasoning.
 *  Collapse rules: completed stages collapsed by default; live, failed and
 *  approval-waiting segments stay expanded. */

type ToolKind = "file" | "search" | "command" | "other";
const TOOL_ICON: Record<ToolKind, string> = { file: "📄", search: "🔍", command: "⌨", other: "◆" };

/** Tool-name → card kind, from the tool field the events already carry.
 *  Unknown tools stay "other" and render their raw label. */
export function toolKind(tool: string): ToolKind {
  const s = tool.toLowerCase();
  if (/file|read|write|edit|patch|apply/.test(s)) return "file";
  if (/search|grep|find|web|fetch|lookup/.test(s)) return "search";
  if (/run|exec|shell|bash|command|terminal|npm|python/.test(s)) return "command";
  return "other";
}

interface RanItem { kind: "ran"; key: string; tool: ToolKind; label: string; detail: string; done: boolean }
interface ThoughtItem { kind: "thought"; key: string; role: string; text: string }
interface RowItem { kind: "row"; key: string; label: string; done: boolean; attention: boolean; failed: boolean }
type SegItem = RanItem | ThoughtItem | RowItem;

interface Seg {
  kind: "seg";
  key: string;
  phase: string;
  seconds: number | null;
  live: boolean;
  attention: boolean;
  items: SegItem[];
}

type Item = Seg | SegItem;

function secsBetween(start: string | undefined, end: string | undefined): number | null {
  if (!start || !end) return null;
  const ms = Date.parse(end) - Date.parse(start);
  return Number.isFinite(ms) && ms >= 0 ? Math.round(ms / 100) / 10 : null;
}

const row = (e: RunEventView, label: string, done = true, attention = false, failed = false): RowItem =>
  ({ kind: "row", key: e.eventId, label, done, attention, failed });

function buildItems(events: RunEventView[] | undefined, messages: ConversationMessageView[]): Item[] {
  const items: Item[] = [];
  const openSegs = new Map<string, Seg>();
  const openRuns = new Map<string, RanItem>();
  let cur: Seg | null = null;
  const sink = (item: SegItem) => {
    if (item.kind === "row" && item.attention && cur) cur.attention = true;
    if (cur) cur.items.push(item); else items.push(item);
  };

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
        sink(row(e, t("think.task-created")));
        break;
      case "run_started":
        sink(row(e, t("think.task-started")));
        break;
      case "run_completed":
        sink(row(e, t("think.task-completed")));
        break;
      case "run_failed":
        sink(row(e, t("think.task-failed"), true, true, true));
        break;
      case "run_cancelled":
      case "run_cancelling":
        sink(row(e, t("think.task-cancelled")));
        break;
      case "stage_started": {
        const seg: Seg = {
          kind: "seg",
          key: e.eventId,
          phase: String(p.phase ?? p.stage_id ?? "stage"),
          seconds: null,
          live: true,
          attention: false,
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
        const tool = String(p.tool ?? "");
        const target = String(p.target ?? "");
        const ran: RanItem = {
          kind: "ran",
          key: e.eventId,
          tool: toolKind(tool),
          label: t("think.ran", { cmd: `${tool || String(p.verb ?? "task")}${target ? ` ${target}` : ""}` }),
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
        sink(row(e, t("think.approval-requested", { action: String(p.action ?? p.risk_level ?? "") }), false, true));
        break;
      case "approval.decided":
        sink(row(e, t("think.approval-decided", { decision: String(p.decision ?? "") })));
        break;
      case "run_waiting_user":
      case "run_blocked":
        sink(row(e, t("think.waiting-user"), false, true));
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
  const title = seg.live ? t("think.working", { phase: seg.phase }) : t("think.worked", { s: seg.seconds ?? "?", phase: seg.phase });
  // completed stages collapse; live / failed / approval-waiting stay open
  return (
    <details className={`think-seg ${seg.live ? "live" : ""} ${seg.attention ? "attention" : ""}`} open={seg.live || seg.attention}>
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
        <summary>{t("think.thoughts")}</summary>
        <p>{item.text}</p>
      </details>
    );
  }
  if (item.kind === "ran") {
    return (
      <div className={`think-ran tool-${item.tool} ${item.done ? "done" : "live"}`}>
        <span className="think-dot">{item.done ? "✓" : "◐"}</span>
        <span className="think-kind">{TOOL_ICON[item.tool]}</span>
        <span className="think-cmd">{item.label}</span>
      </div>
    );
  }
  return (
    <div className={`think-row ${item.done ? "done" : "live"} ${item.attention ? "attention" : ""}`}>
      <span className="think-dot">{item.done ? "✓" : "◐"}</span>
      <span>{item.label}</span>
    </div>
  );
}

export function ThinkingChain({
  events,
  messages = [],
  canResume = false,
  onResume,
}: {
  events: RunEventView[] | undefined;
  messages?: ConversationMessageView[];
  canResume?: boolean;
  onResume?: () => void;
}) {
  const items = buildItems(events, messages);
  if (items.length === 0) return null;
  const live = items.some((i) => (i.kind === "seg" ? i.live : i.kind !== "thought" && !i.done));
  const total = countSteps(items);
  const failed = items.some((i) => i.kind === "row" && i.failed);
  return (
    <details className="msg3 thinking" open={live || failed}>
      <summary>
        <span className="think-glyph">◈</span> {t("think.title")}
        <em>{total ? t("think.items", { n: total }) : ""}</em>
      </summary>
      <div className="think-list">
        {items.map((item) => (
          item.kind === "seg" ? <SegView key={item.key} seg={item} /> : <ItemView item={item} key={item.key} />
        ))}
      </div>
      {failed && canResume && onResume && (
        <button className="btn secondary think-resume" onClick={onResume} type="button">{t("think.resume")}</button>
      )}
    </details>
  );
}
