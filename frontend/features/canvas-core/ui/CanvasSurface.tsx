/**
 * Canvas-core React Flow surface (Phase 1 Week 2).
 *
 * Renders the in-memory canvas from `useCanvasCoreStore` via React Flow.
 * Pure presentational binding — node/edge/viewport changes route back
 * into the store, which owns the persist debouncer.
 *
 * Selection, framed selection, and the smart-mode node palette are
 * deferred to follow-ups; this PR ships the wiring that the rest of
 * Phase 1 builds on.
 */

import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  SelectionMode,
  type Connection,
  type EdgeChange,
  type IsValidConnection,
  type Node,
  type NodeChange,
  type OnSelectionChangeParams,
  type Viewport,
  applyEdgeChanges,
  applyNodeChanges,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useCallback, useMemo } from 'react';

import type { CanvasConnection, CanvasNode } from '../types';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { SMART_NODE_TYPES } from '../smart/nodes/registry';
import { CLASSIC_NODE_TYPES } from '../classic/ClassicNodeViews';
import { toReactFlowEdges, validateCanvasConnection } from './connectionMapping';

type AnyNode = Node;

// MiniMap renders via SVG; CSS custom-properties are not available in that
// context, so we carry the hex values directly.  Both literals are derived
// from the ink palette dark-mode defaults (index.css § ink ladder):
//   INK_ACCENT → --color-accent  (#6366f1, indigo-500)   — selected nodes
//   INK_MUTED  → dark-mode --ink-600  (#52525b, zinc-600) — unselected nodes
const INK_ACCENT = '#6366f1';
const INK_MUTED = '#52525b';

function toReactFlowNodes(nodes: CanvasNode[]): AnyNode[] {
  return nodes.map((node, idx) => {
    const obj = node as Record<string, unknown>;
    const id = typeof obj.id === 'string' ? obj.id : `node-${idx}`;
    const position =
      obj.position && typeof obj.position === 'object'
        ? (obj.position as { x: number; y: number })
        : { x: 0, y: 0 };
    const type = typeof obj.type === 'string' ? obj.type : 'default';
    const data =
      obj.data && typeof obj.data === 'object'
        ? (obj.data as Record<string, unknown>)
        : { label: typeof obj.label === 'string' ? obj.label : id };
    return { id, position, type, data } as AnyNode;
  });
}

export function CanvasSurface() {
  const nodes = useCanvasCoreStore((s) => s.nodes);
  const connections = useCanvasCoreStore((s) => s.connections);
  const viewport = useCanvasCoreStore((s) => s.viewport);
  const selection = useCanvasCoreStore((s) => s.selection);
  const kind = useCanvasCoreStore((s) => s.kind);
  const setNodes = useCanvasCoreStore((s) => s.setNodes);
  const setConnections = useCanvasCoreStore((s) => s.setConnections);
  const setViewport = useCanvasCoreStore((s) => s.setViewport);
  const setSelection = useCanvasCoreStore((s) => s.setSelection);

  const selectionSet = useMemo(() => new Set(selection), [selection]);

  // nodeColor for MiniMap — selected nodes get the app accent, others get muted.
  const nodeColor = useCallback(
    (n: AnyNode) => (selectionSet.has(n.id) ? INK_ACCENT : INK_MUTED),
    [selectionSet],
  );

  const rfNodes = useMemo(
    () =>
      toReactFlowNodes(nodes).map((n) => ({
        ...n,
        selected: selectionSet.has(n.id),
      })),
    [nodes, selectionSet],
  );
  const rfEdges = useMemo(() => toReactFlowEdges(connections), [connections]);

  const onNodesChange = useCallback(
    (changes: NodeChange[]) => {
      const next = applyNodeChanges(changes, rfNodes);
      setNodes(next as unknown as CanvasNode[]);
    },
    [rfNodes, setNodes],
  );

  const onEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      const next = applyEdgeChanges(changes, rfEdges);
      setConnections(next as unknown as CanvasConnection[]);
    },
    [rfEdges, setConnections],
  );

  const onMove = useCallback(
    (_event: unknown, nextViewport: Viewport) => {
      setViewport(nextViewport);
    },
    [setViewport],
  );

  const onSelectionChange = useCallback(
    (params: OnSelectionChangeParams) => {
      const ids = params.nodes.map((n) => n.id);
      setSelection(ids);
    },
    [setSelection],
  );

  // Shared node-type lookup — the single source the validator reads, used by
  // BOTH the live drag-validity hint (`isConnectionValid`) and the commit
  // (`onConnect`), so the dropped-wire rule and the appended-edge rule can
  // never diverge.
  const nodeTypeById = useCallback(
    (id: string) => rfNodes.find((n) => n.id === id)?.type,
    [rfNodes],
  );

  const isConnectionValid = useCallback<IsValidConnection>(
    (connection) => validateCanvasConnection(connection, kind, nodeTypeById),
    [kind, nodeTypeById],
  );

  // Commit a dragged wire. React Flow hands us {source, target, sourceHandle,
  // targetHandle}; we re-run the SAME validator as the live hint (smart →
  // canConnectSmart, classic → typed-port canConnectClassic). Valid wires are
  // appended to the store as a new `CanvasConnection` carrying a generated id
  // and the handle ids (critical: the cascade + multi-port validation address
  // ports by handle, and `toReactFlowEdges` round-trips them). Invalid wires
  // are dropped silently — there was no edge before, so dropping is a no-op.
  const onConnect = useCallback(
    (connection: Connection) => {
      if (!validateCanvasConnection(connection, kind, nodeTypeById)) return;
      const newEdge: CanvasConnection = {
        id: `edge-${crypto.randomUUID()}`,
        source: connection.source,
        target: connection.target,
        sourceHandle: connection.sourceHandle ?? null,
        targetHandle: connection.targetHandle ?? null,
      };
      setConnections([...connections, newEdge]);
    },
    [kind, nodeTypeById, connections, setConnections],
  );

  const nodeTypes =
    kind === 'smart'
      ? SMART_NODE_TYPES
      : kind === 'classic'
        ? CLASSIC_NODE_TYPES
        : undefined;

  return (
    <ReactFlow
      nodes={rfNodes}
      edges={rfEdges}
      nodeTypes={nodeTypes}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onConnect={onConnect}
      onMove={onMove}
      onSelectionChange={onSelectionChange}
      isValidConnection={isConnectionValid}
      viewport={viewport}
      fitView={false}
      proOptions={{ hideAttribution: true }}
      // 6b.1 — skip rendering nodes/edges whose bounding box lies outside
      // the current viewport.  React Flow re-checks on every pan/zoom so
      // the visible set stays correct without any extra work from us.
      onlyRenderVisibleElements
      // Shift-drag for box select; click-drag is for panning the
      // viewport, matching Figma / Miro / Excalidraw.
      selectionMode={SelectionMode.Partial}
      selectionOnDrag={false}
      multiSelectionKeyCode="Shift"
      deleteKeyCode={null}
    >
      <Background variant={BackgroundVariant.Dots} gap={20} size={1} />
      <Controls position="bottom-right" />
      {/* 6b.2 — minimap: pannable + zoomable, selected nodes highlighted */}
      <MiniMap
        position="bottom-left"
        pannable
        zoomable
        nodeColor={nodeColor}
        nodeStrokeWidth={3}
      />
    </ReactFlow>
  );
}
