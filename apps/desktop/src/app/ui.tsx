import { useState, type ReactElement, type ReactNode, type SVGProps } from "react";
import type { EventTone, RunStatus, StageStatus } from "../types";
import { AgentServiceClientError, type ServiceErrorDTO } from "../types/service";
import { t, type I18nKey } from "./i18n";

export type InspectorTab = "files" | "events";

export type IconName =
  | "activity"
  | "arrow-up-right"
  | "box"
  | "check"
  | "chevron-down"
  | "chevron-right"
  | "clock"
  | "code"
  | "copy"
  | "file"
  | "folder"
  | "layers"
  | "play"
  | "plus"
  | "refresh"
  | "search"
  | "send"
  | "settings"
  | "shield"
  | "spark"
  | "terminal"
  | "x";

export const iconPaths: Record<IconName, string> = {
  activity: "M3 12h3l2-7 4 14 2-7h7",
  "arrow-up-right": "M7 17 17 7M9 7h8v8",
  box: "m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Z M4 7.5l8 4.5 8-4.5 M12 12v9",
  check: "m5 12 4 4L19 6",
  "chevron-down": "m6 9 6 6 6-6",
  "chevron-right": "m9 6 6 6-6 6",
  clock: "M12 7v5l3 2 M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z",
  code: "m8 9-4 3 4 3M16 9l4 3-4 3M14 5l-4 14",
  copy: "M8 8V5h11v11h-3 M5 8h11v11H5z",
  file: "M6 3h8l4 4v14H6z M14 3v5h5 M9 13h6M9 17h6",
  folder: "M3 6h7l2 2h9v10H3z",
  layers: "m12 3 9 5-9 5-9-5 9-5ZM3 12l9 5 9-5M3 16l9 5 9-5",
  play: "m8 5 11 7-11 7V5Z",
  plus: "M12 5v14M5 12h14",
  refresh: "M20 11a8 8 0 1 0 1 5M20 5v6h-6",
  search: "m20 20-4.5-4.5M10.5 17a6.5 6.5 0 1 1 0-13 6.5 6.5 0 0 1 0 13Z",
  send: "m21 3-7.5 18-3.4-7.1L3 10.5 21 3ZM10.1 13.9 21 3",
  settings: "M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7ZM19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-1.7 1.7-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5v.2h-2.4v-.2a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1L8 17l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H6v-2.4h.2a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.9L7.3 8.6 9 6.9l.1.1a1.7 1.7 0 0 0 1.9.3 1.7 1.7 0 0 0 1-1.5v-.2h2.4v.2a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l-.1-.1 1.7 1.7-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.5 1h.2V14h-.2a1.7 1.7 0 0 0-1.5 1Z",
  shield: "M12 3 20 6v5c0 5.1-3.4 8.7-8 10-4.6-1.3-8-4.9-8-10V6l8-3Z M9 12l2 2 4-4",
  spark: "m12 2 1.6 6.4L20 10l-6.4 1.6L12 18l-1.6-6.4L4 10l6.4-1.6L12 2ZM19 16l.7 2.3L22 19l-2.3.7L19 22l-.7-2.3L16 19l2.3-.7L19 16Z",
  terminal: "M4 5h16v14H4z M7 9l3 3-3 3M12 15h4",
  x: "m6 6 12 12M18 6 6 18",
};

/** Relative "n分钟前" stamp for message and list rows. */
export const rel = (iso: string) => {
  const d = Date.now() - Date.parse(iso);
  if (!iso || Number.isNaN(d)) return "";
  const m = Math.floor(d / 60000);
  return m < 1 ? "刚刚" : m < 60 ? `${m}分钟前` : m < 1440 ? `${Math.floor(m / 60)}小时前` : `${Math.floor(m / 1440)}天前`;
};

/** Localized status labels — one vocabulary across timeline, buttons and
 *  summaries so cancelling / cancelled / waiting-approval / failed never blur. */
export function statusLabel(status: RunStatus): string {
  return t(`status.${status}` as I18nKey);
}

export function stageLabel(status: StageStatus): string {
  return t(`stage.${status}` as I18nKey);
}

export const eventIcons: Record<EventTone, IconName> = {
  info: "activity",
  success: "check",
  warning: "box",
  error: "x",
  neutral: "clock",
};

export function toServiceError(error: unknown): ServiceErrorDTO {
  if (error instanceof AgentServiceClientError) return error.toDTO();
  return {
    code: "INTERNAL_ERROR",
    message: "AgentService returned an unexpected error.",
    retryable: false,
  };
}

export function requestIdForRun(sequenceHint: number): string {
  const randomId = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${sequenceHint}`;
  return `desktop-${randomId}`;
}

export function Icon({ name, size = 16, className = "", ...props }: { name: IconName; size?: number; className?: string } & SVGProps<SVGSVGElement>) {
  return (
    <svg
      aria-hidden="true"
      className={`icon ${className}`}
      fill="none"
      height={size}
      viewBox="0 0 24 24"
      width={size}
      {...props}
    >
      <path d={iconPaths[name]} stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
    </svg>
  );
}

/** §S3 status ping — one expanding ring on each status change. */
export function StatusBadge({ status }: { status: RunStatus }) {
  return <span className={`status-badge ${status} st-ping`}>{statusLabel(status)}</span>;
}

export function stageIcon(status: StageStatus): IconName {
  if (status === "completed" || status === "verified") return "check";
  if (status === "running") return "play";
  if (status === "interrupted") return "box";
  if (status === "failed") return "x";
  return "clock";
}


/** Markdown-lite renderer — headings/lists/quote/hr/bold/italic/inline-code/
 *  code-block/links/para, no deps. */
/** Shared Markdown renderer — lists, GFM tables, links, highlighted code
 *  blocks with copy feedback. Everything renders as React text nodes so
 *  model-output HTML is never executed. */

/** Lightweight token highlighter — comments, strings, numbers and a shared
 *  keyword set (js/ts/py/sh/json). Honest scope: no per-language grammar. */
const MD_TOK = /(\/\/[^\n]*|#[^\n]*|\/\*[\s\S]*?\*\/)|("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`)|(\b\d[\d_]*(?:\.\d+)?\b)|([A-Za-z_$][\w$]*)/g;
const MD_KW = new Set(
  ("const let var function return if else for while class new import export from async await try catch finally throw typeof instanceof " +
   "switch case default break continue extends implements this self null none nil undefined true false " +
   "def lambda print yield raise with as pass elif is not and or in of do done then fi esac echo eval " +
   "public private protected static readonly interface type enum struct fn mut pub use mod impl match")
    .split(" "),
);

export function highlightCode(code: string): ReactNode[] {
  const out: ReactNode[] = [];
  let k = 0; let last = 0;
  for (const m of code.matchAll(MD_TOK)) {
    const i = m.index ?? 0;
    if (i > last) out.push(code.slice(last, i));
    const cls = m[1] ? "md-tok-com" : m[2] ? "md-tok-str" : m[3] ? "md-tok-num" : MD_KW.has(m[4]) ? "md-tok-kw" : null;
    out.push(cls ? <span className={cls} key={k++}>{m[0]}</span> : m[0]);
    last = i + m[0].length;
  }
  if (last < code.length) out.push(code.slice(last));
  return out;
}

function MdCodeBlock({ lang, code }: { lang: string; code: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <pre className="md-code">
      {lang && <span className="md-lang">{lang}</span>}
      <button className="md-copy" onClick={() => {
        void navigator.clipboard.writeText(code).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1400);
        });
      }} type="button">{copied ? t("common.copied") : t("common.copy")}</button>
      <code>{highlightCode(code)}</code>
    </pre>
  );
}

export function MarkdownText({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  const parts = text.split(/```(\w*)\n?([\s\S]*?)```/g);
  for (let i = 0; i < parts.length; i += 3) {
    const prose = parts[i];
    if (prose.trim()) blocks.push(<div className="md-body" key={i}>{renderProse(prose)}</div>);
    if (parts[i + 1] !== undefined && parts[i + 2] !== undefined) {
      blocks.push(<MdCodeBlock code={parts[i + 2]} key={`c${i}`} lang={parts[i + 1] ?? ""} />);
    }
  }
  return <>{blocks}</>;
}

/** Block-level: headings, tables, hr, blockquote, ul/ol lists, else paragraph. */
function renderProse(prose: string): ReactNode[] {
  const out: ReactNode[] = [];
  const lines = prose.split("\n");
  let para: string[] = []; let list: { ordered: boolean; items: string[] } | null = null; let k = 0;
  const flushPara = () => { if (para.length) { out.push(<p key={k++}>{renderInline(para.join("\n"))}</p>); para = []; } };
  const flushList = () => {
    if (list) {
      out.push(list.ordered
        ? <ol className="md-list" key={k++}>{list.items.map((it, j) => <li key={j}>{renderInline(it)}</li>)}</ol>
        : <ul className="md-list" key={k++}>{list.items.map((it, j) => <li key={j}>{renderInline(it)}</li>)}</ul>);
      list = null;
    }
  };
  const tableSep = (line: string) => /^\s*\|?[\s:|-]+\|?\s*$/.test(line) && line.includes("-");
  const tableCells = (line: string) => line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
  for (let li = 0; li < lines.length; li++) {
    const line = lines[li];
    // GFM table: header row, |---| separator, body rows
    if (line.includes("|") && li + 1 < lines.length && tableSep(lines[li + 1])) {
      flushPara(); flushList();
      const head = tableCells(line);
      const rows: string[][] = [];
      li += 2;
      while (li < lines.length && lines[li].includes("|") && lines[li].trim()) {
        const cells = tableCells(lines[li]);
        while (cells.length < head.length) cells.push("");
        rows.push(cells.slice(0, head.length));
        li++;
      }
      li--;
      out.push(
        <table className="md-table" key={k++}>
          <thead><tr>{head.map((c, j) => <th key={j}>{renderInline(c)}</th>)}</tr></thead>
          <tbody>{rows.map((r, j) => <tr key={j}>{r.map((c, i) => <td key={i}>{renderInline(c)}</td>)}</tr>)}</tbody>
        </table>
      );
      continue;
    }
    const ul = line.match(/^\s*[-*•]\s+(.+)/);
    const ol = line.match(/^\s*\d+[.)]\s+(.+)/);
    const h = line.match(/^(#{1,4})\s+(.+)/);
    if (ul || ol) {
      flushPara();
      const ordered = Boolean(ol);
      const item = (ul ?? ol)![1];
      if (!list || list.ordered !== ordered) { flushList(); list = { ordered, items: [] }; }
      list!.items.push(item);
      continue;
    }
    flushList();
    if (h) { flushPara(); const lv = Math.min(h[1].length, 4); out.push(<div className={`md-h md-h${lv}`} key={k++}>{renderInline(h[2])}</div>); continue; }
    if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) { flushPara(); out.push(<hr className="md-hr" key={k++} />); continue; }
    if (line.trim().startsWith(">")) { flushPara(); out.push(<blockquote className="md-quote" key={k++}>{renderInline(line.trim().slice(1).trim())}</blockquote>); continue; }
    if (!line.trim()) { flushPara(); continue; }
    para.push(line);
  }
  flushPara(); flushList();
  return out;
}

function renderInline(s: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|\*[^*\n]+\*|`[^`]+`|\[[^\]]+\]\([^)]+\)|\n)/g;
  let m: RegExpExecArray | null; let k = 0; let last = 0;
  while ((m = re.exec(s))) {
    if (m.index > last) out.push(s.slice(last, m.index));
    const [t] = m;
    if (t === "\n") out.push(<br key={k++} />);
    else if (t.startsWith("**")) out.push(<strong key={k++}>{t.slice(2, -2)}</strong>);
    else if (t.startsWith("*")) out.push(<em key={k++}>{t.slice(1, -1)}</em>);
    else if (t.startsWith("[")) { const lm = t.match(/^\[([^\]]+)\]\(([^)]+)\)$/); out.push(lm ? <a className="md-link" href={lm[2]} key={k++} rel="noreferrer" target="_blank">{lm[1]}</a> : t); }
    else out.push(<code className="md-inline" key={k++}>{t.slice(1, -1)}</code>);
    last = m.index + t.length;
  }
  if (last < s.length) out.push(s.slice(last));
  return out;
}
