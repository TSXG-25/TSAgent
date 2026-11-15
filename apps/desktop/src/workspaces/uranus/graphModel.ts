import type { GraphRunStepSummary } from "../../types/service";

/** Uranus canvas model — mirrors the persisted workflow-graph definition
 *  contract. Task nodes are delegated child runs; edges are {from,to}
 *  success dependencies; input nodes feed {name} substitution.
 *  x/y are canvas coordinates persisted verbatim in the definition. */
export interface CanvasNodeData {
  id: string;
  type: "task" | "input";
  requestText: string;
  profileId: string;
  targetPlanet: string;
  name: string;
  value: string;
  x: number;
  y: number;
  /** Unrecognized definition fields ride through save round-trips. */
  extra: Record<string, unknown>;
}

export interface CanvasEdge {
  from: string;
  to: string;
}

export interface CanvasGraph {
  nodes: CanvasNodeData[];
  edges: CanvasEdge[];
}

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const str = (v: unknown): string => (typeof v === "string" ? v : "");
const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

/** Longest-path layering so saved graphs without coordinates still lay out
 *  as a readable left-to-right DAG. */
function autoLayout(nodes: CanvasNodeData[], edges: CanvasEdge[]): void {
  const rank = new Map<string, number>();
  const preds = new Map<string, string[]>();
  for (const e of edges) {
    preds.set(e.to, [...(preds.get(e.to) ?? []), e.from]);
  }
  const rankOf = (id: string, seen: Set<string>): number => {
    if (rank.has(id)) return rank.get(id) ?? 0;
    if (seen.has(id)) return 0;
    seen.add(id);
    const r = Math.max(0, ...(preds.get(id) ?? []).map((p) => rankOf(p, seen) + 1));
    rank.set(id, r);
    return r;
  };
  const perRank = new Map<number, number>();
  for (const n of nodes) {
    const r = rankOf(n.id, new Set());
    const slot = perRank.get(r) ?? 0;
    perRank.set(r, slot + 1);
    n.x = 60 + r * 250;
    n.y = 60 + slot * 120;
  }
}

export function parseGraphDefinition(definitionJson: string): CanvasGraph {
  let parsed: unknown;
  try {
    parsed = JSON.parse(definitionJson);
  } catch {
    return { nodes: [], edges: [] };
  }
  if (!isRecord(parsed)) return { nodes: [], edges: [] };
  const nodes: CanvasNodeData[] = [];
  let needsLayout = false;
  for (const raw of Array.isArray(parsed.nodes) ? parsed.nodes : []) {
    if (!isRecord(raw)) continue;
    const type = raw.type === "input" ? "input" : "task";
    const x = num(raw.x);
    const y = num(raw.y);
    if (x === null || y === null) needsLayout = true;
    nodes.push({
      id: str(raw.id),
      type,
      requestText: str(raw.request_text),
      profileId: str(raw.profile_id),
      targetPlanet: str(raw.target_planet),
      name: str(raw.name),
      value: str(raw.value),
      x: x ?? 0,
      y: y ?? 0,
      extra: Object.fromEntries(
        Object.entries(raw).filter(
          ([k]) => !["id", "type", "request_text", "profile_id", "target_planet", "name", "value", "x", "y"].includes(k),
        ),
      ),
    });
  }
  const edges: CanvasEdge[] = [];
  const ids = new Set(nodes.map((n) => n.id));
  for (const raw of Array.isArray(parsed.edges) ? parsed.edges : []) {
    if (!isRecord(raw)) continue;
    const from = str(raw.from);
    const to = str(raw.to);
    if (ids.has(from) && ids.has(to)) edges.push({ from, to });
  }
  if (needsLayout) autoLayout(nodes, edges);
  return { nodes, edges };
}

export function serializeGraphDefinition(graph: CanvasGraph): Record<string, unknown> {
  return {
    nodes: graph.nodes.map((n) => ({
      ...n.extra,
      id: n.id,
      type: n.type,
      ...(n.type === "task"
        ? {
            request_text: n.requestText,
            profile_id: n.profileId,
            target_planet: n.targetPlanet,
          }
        : { name: n.name, value: n.value }),
      x: Math.round(n.x),
      y: Math.round(n.y),
    })),
    edges: graph.edges.map((e) => ({ from: e.from, to: e.to })),
  };
}

export function emptyCanvas(): CanvasGraph {
  return { nodes: [], edges: [] };
}

export function nextNodeId(nodes: CanvasNodeData[]): string {
  let max = 0;
  for (const n of nodes) {
    const m = /^n(\d+)$/.exec(n.id);
    if (m) max = Math.max(max, Number(m[1]));
  }
  return `n${max + 1}`;
}

/** Canvas edit events translated from the flow renderer — kept free of
 *  @xyflow types so the pure contract is unit-testable. */
export interface NodeDelta {
  kind: "position" | "remove" | "select";
  id: string;
  position?: { x: number; y: number };
  selected?: boolean;
}

export interface EdgeDelta {
  kind: "remove" | "select";
  id: string;
  selected?: boolean;
}

export interface NodeDeltaResult {
  graph: CanvasGraph;
  /** undefined = no select deltas; null = everything deselected. */
  selectNext: string | null | undefined;
}

/** Controlled-mode contract: position/remove/select deltas must be
 *  written back into the model — notifications alone don't move RF
 *  state. Returns the updated graph plus the resulting node selection. */
export function applyNodeDeltas(graph: CanvasGraph, deltas: NodeDelta[]): NodeDeltaResult {
  const removed = new Set<string>();
  const moved = new Map<string, { x: number; y: number }>();
  let selectNext: string | null | undefined;
  for (const d of deltas) {
    if (d.kind === "remove") removed.add(d.id);
    else if (d.kind === "position" && d.position) moved.set(d.id, d.position);
    else if (d.kind === "select") {
      selectNext = d.selected ? d.id : selectNext === undefined ? null : selectNext;
    }
  }
  if (removed.has(selectNext ?? "")) selectNext = null;
  const changed = removed.size > 0 || moved.size > 0;
  return {
    graph: changed
      ? {
          nodes: graph.nodes
            .filter((n) => !removed.has(n.id))
            .map((n) => (moved.has(n.id) ? { ...n, ...moved.get(n.id)! } : n)),
          edges: graph.edges.filter((e) => !removed.has(e.from) && !removed.has(e.to)),
        }
      : graph,
    selectNext,
  };
}

export interface EdgeDeltaResult {
  graph: CanvasGraph;
  edgeSel: Set<string>;
}

/** Edge ids are `from->to` — deletion works only for edges whose
 *  `selected` state was written back (controlled contract). */
export function applyEdgeDeltas(
  graph: CanvasGraph,
  deltas: EdgeDelta[],
  edgeSel: Set<string>,
): EdgeDeltaResult {
  const removed = new Set<string>();
  let sel = edgeSel;
  for (const d of deltas) {
    if (d.kind === "select") {
      if (sel === edgeSel) sel = new Set(sel);
      if (d.selected) sel.add(d.id);
      else sel.delete(d.id);
    } else if (d.kind === "remove") {
      removed.add(d.id);
      if (sel === edgeSel) sel = new Set(sel);
      sel.delete(d.id);
    }
  }
  return {
    graph: removed.size
      ? {
          ...graph,
          edges: graph.edges.filter((e) => !removed.has(`${e.from}->${e.to}`)),
        }
      : graph,
    edgeSel: sel,
  };
}

/** Save receipts must bind to the submitted document identity and edit
 *  version — a late response cannot mark newer edits saved or switch the
 *  open graph back to the submitted one. */
export class DraftSaveGate {
  private seq = 0;

  /** Every draft edit (canvas or name) bumps the version. */
  touch(): number {
    return ++this.seq;
  }

  submit(openGraphId: string | null): { seq: number; openGraphId: string | null } {
    return { seq: ++this.seq, openGraphId };
  }

  accept(
    ticket: { seq: number; openGraphId: string | null },
    openGraphId: string | null,
  ): boolean {
    return ticket.seq === this.seq && ticket.openGraphId === openGraphId;
  }
}

/** Draft/edited canvas → node-id lookup for execution overlay. The
 *  execution is a historical fact; only nodes still present show it. */
export function executionStatusByNode(
  steps: GraphRunStepSummary[] | undefined,
): Map<string, GraphRunStepSummary> {
  const map = new Map<string, GraphRunStepSummary>();
  for (const s of steps ?? []) map.set(s.nodeId, s);
  return map;
}
