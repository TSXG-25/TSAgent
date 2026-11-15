import { useCallback, useMemo } from "react";
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
import type { CanvasEdge, CanvasGraph, CanvasNodeData } from "./graphModel";

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
        markerEnd: { type: MarkerType.ArrowClosed },
      })),
    [graph.edges],
  );

  const onNodesChange = useCallback(
    (changes: NodeChange<FlowNode>[]) => {
      const removed = new Set(
        changes.filter((c) => c.type === "remove").map((c) => c.id),
      );
      const moved = new Map<string, { x: number; y: number }>();
      for (const c of changes) {
        if (c.type === "position" && c.position) moved.set(c.id, c.position);
      }
      if (removed.size === 0 && moved.size === 0) return;
      onChange({
        nodes: graph.nodes
          .filter((n) => !removed.has(n.id))
          .map((n) => (moved.has(n.id) ? { ...n, ...moved.get(n.id)! } : n)),
        edges: graph.edges.filter(
          (e) => !removed.has(e.from) && !removed.has(e.to),
        ),
      });
    },
    [graph, onChange],
  );

  const onEdgesChange = useCallback(
    (changes: EdgeChange<Edge>[]) => {
      const removed = new Set(
        changes.filter((c) => c.type === "remove").map((c) => c.id),
      );
      if (removed.size === 0) return;
      onChange({ ...graph, edges: graph.edges.filter((e) => !removed.has(edgeId(e))) });
    },
    [graph, onChange],
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

  const onSelectionChange = useCallback(
    ({ nodes: sel }: { nodes: FlowNode[] }) => {
      onSelect(sel[0]?.id ?? null);
    },
    [onSelect],
  );

  return (
    <ReactFlow
      nodes={rfNodes}
      edges={rfEdges}
      nodeTypes={nodeTypes}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onConnect={onConnect}
      onSelectionChange={onSelectionChange}
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
