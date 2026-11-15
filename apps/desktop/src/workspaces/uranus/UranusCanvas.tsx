import { useCallback, useMemo, useState } from "react";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { t } from "../../app/i18n";
import { PLANET_SCENES } from "../../app/scenes";
import type { GraphRunStepSummary } from "../../types/service";
import {
  applyEdgeDeltas,
  applyNodeDeltas,
  type CanvasEdge,
  type CanvasGraph,
  type CanvasNodeData,
  type EdgeDelta,
  type NodeDelta,
} from "./graphModel";

type FlowNode = Node<{ node: CanvasNodeData; step?: GraphRunStepSummary }, "uranusNode">;

const STATUS_CLASS: Record<string, string> = {
  COMPLETED: "ok",
  FAILED: "bad",
  FAILED_TERMINAL: "bad",
  RUNNING: "run",
  PENDING: "wait",
  ADMITTED: "wait",
  SKIPPED: "skip",
  INTERRUPTED: "bad",
  CANCELLED: "bad",
};

function statusClass(status: string): string {
  return STATUS_CLASS[status] ?? (status.startsWith("FAILED") ? "bad" : "wait");
}

function UranusNode({ data, selected }: NodeProps<FlowNode>) {
  const { node, step } = data;
  const planet = PLANET_SCENES.find((s) => s.planet === node.targetPlanet);
  return (
    <div className={`ura-nodecard${selected ? " sel" : ""} ${node.type === "input" ? "is-input" : ""}`}>
      <Handle type="target" position={Position.Left} />
      <div className="ura-nodecard-head">
        <span className="ura-nodecard-id">{node.type === "input" ? `{${node.name || node.id}}` : node.id}</span>
        {step?.status && (
          <span className={`ura-badge ${statusClass(step.status)}`} title={step.detail || step.status}>
            {step.status}
          </span>
        )}
      </div>
      <div className="ura-nodecard-body">
        {node.type === "input"
          ? (node.value || "—")
          : (node.requestText || t("ura.node-empty"))}
      </div>
      <div className="ura-nodecard-meta">
        {node.profileId && <span className="ura-chip">{node.profileId}</span>}
        {planet && <span className="ura-chip">{planet.name}</span>}
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

const nodeTypes = { uranusNode: UranusNode };

const edgeId = (e: CanvasEdge) => `${e.from}->${e.to}`;

/** Dify-style DAG canvas: drag nodes, draw edges between handles, delete
 *  selection with Backspace/Delete, zoom and fit. All edits flow back to
 *  the workspace as CanvasGraph changes. */
export function UranusCanvas({
  graph,
  statusByNode,
  selectedId,
  onChange,
  onSelect,
}: {
  graph: CanvasGraph;
  statusByNode: Map<string, GraphRunStepSummary>;
  selectedId: string | null;
  onChange: (next: CanvasGraph) => void;
  onSelect: (id: string | null) => void;
}) {
  /** Edge selection is renderer-local state — needed so Delete deletes
   *  the edge the user actually selected (controlled-mode contract:
   *  select changes must be written back). */
  const [edgeSel, setEdgeSel] = useState<Set<string>>(new Set());

  const rfNodes = useMemo<FlowNode[]>(
    () =>
      graph.nodes.map((n) => ({
        id: n.id,
        type: "uranusNode",
        position: { x: n.x, y: n.y },
        selected: n.id === selectedId,
        data: { node: n, step: statusByNode.get(n.id) },
      })),
    [graph.nodes, selectedId, statusByNode],
  );
  const rfEdges = useMemo<Edge[]>(
    () =>
      graph.edges.map((e) => ({
        id: edgeId(e),
        source: e.from,
        target: e.to,
        selected: edgeSel.has(edgeId(e)),
        markerEnd: { type: MarkerType.ArrowClosed },
      })),
    [graph.edges, edgeSel],
  );

  const onNodesChange = useCallback(
    (changes: NodeChange<FlowNode>[]) => {
      const deltas: NodeDelta[] = [];
      for (const c of changes) {
        if (c.type === "position" && c.position) {
          deltas.push({ kind: "position", id: c.id, position: c.position });
        } else if (c.type === "remove") {
          deltas.push({ kind: "remove", id: c.id });
        } else if (c.type === "select") {
          deltas.push({ kind: "select", id: c.id, selected: c.selected });
        }
      }
      const { graph: next, selectNext } = applyNodeDeltas(graph, deltas);
      if (next !== graph) onChange(next);
      if (selectNext !== undefined) onSelect(selectNext);
    },
    [graph, onChange, onSelect],
  );

  const onEdgesChange = useCallback(
    (changes: EdgeChange<Edge>[]) => {
      const deltas: EdgeDelta[] = changes
        .filter((c) => c.type === "remove" || c.type === "select")
        .map((c) => ({
          kind: c.type,
          id: c.id,
          selected: c.type === "select" ? c.selected : undefined,
        }));
      const { graph: next, edgeSel: sel } = applyEdgeDeltas(graph, deltas, edgeSel);
      if (sel !== edgeSel) setEdgeSel(sel);
      if (next !== graph) {
        onChange(next);
        setEdgeSel((prev) => new Set([...prev].filter((id) => next.edges.some((e) => edgeId(e) === id))));
      }
    },
    [graph, onChange, edgeSel],
  );

  const onConnect = useCallback(
    (conn: Connection) => {
      if (!conn.source || !conn.target || conn.source === conn.target) return;
      const next: CanvasEdge = { from: conn.source, to: conn.target };
      if (graph.edges.some((e) => e.from === next.from && e.to === next.to)) return;
      onChange({ ...graph, edges: [...graph.edges, next] });
    },
    [graph, onChange],
  );

  return (
    <ReactFlow
      nodes={rfNodes}
      edges={rfEdges}
      nodeTypes={nodeTypes}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onConnect={onConnect}
      fitView
      proOptions={{ hideAttribution: true }}
      deleteKeyCode={["Backspace", "Delete"]}
      colorMode="dark"
    >
      <Background gap={20} />
      <Controls showInteractive={false} />
    </ReactFlow>
  );
}
