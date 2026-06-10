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
  SelectionMode,
  type Edge,
  type EdgeChange,
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

type AnyNode = Node;
type AnyEdge = Edge;

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

function toReactFlowEdges(connections: CanvasConnection[]): AnyEdge[] {
  return connections.map((conn, idx) => {
    const obj = conn as Record<string, unknown>;
    const id = typeof obj.id === 'string' ? obj.id : `edge-${idx}`;
    const source = typeof obj.source === 'string' ? obj.source : '';
    const target = typeof obj.target === 'string' ? obj.target : '';
    return { id, source, target } as AnyEdge;
  });
}

export function CanvasSurface() {
  const nodes = useCanvasCoreStore((s) => s.nodes);
  const connections = useCanvasCoreStore((s) => s.connections);
  const viewport = useCanvasCoreStore((s) => s.viewport);
  const selection = useCanvasCoreStore((s) => s.selection);
  const setNodes = useCanvasCoreStore((s) => s.setNodes);
  const setConnections = useCanvasCoreStore((s) => s.setConnections);
  const setViewport = useCanvasCoreStore((s) => s.setViewport);
  const setSelection = useCanvasCoreStore((s) => s.setSelection);

  const selectionSet = useMemo(() => new Set(selection), [selection]);
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

  return (
    <ReactFlow
      nodes={rfNodes}
      edges={rfEdges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onMove={onMove}
      onSelectionChange={onSelectionChange}
      viewport={viewport}
      fitView={false}
      proOptions={{ hideAttribution: true }}
      // Shift-drag for box select; click-drag is for panning the
      // viewport, matching Figma / Miro / Excalidraw.
      selectionMode={SelectionMode.Partial}
      selectionOnDrag={false}
      multiSelectionKeyCode="Shift"
      deleteKeyCode={null}
    >
      <Background variant={BackgroundVariant.Dots} gap={20} size={1} />
      <Controls position="bottom-right" />
    </ReactFlow>
  );
}
