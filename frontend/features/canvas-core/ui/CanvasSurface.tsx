/**
 * Canvas-core React Flow surface (Phase 1 Week 2).
 *
 * A thin adapter over the shared `CanvasEngine` (canvas-kit): it binds the
 * in-memory canvas from `useCanvasCoreStore` to the engine's controlled props,
 * translates React Flow changes back into the store (which owns the persist
 * debouncer), and injects the canvas-core specifics the engine stays agnostic
 * of — kind → nodeTypes, typed-port connection validation, and the
 * drag-to-create node picker (`DragCreateMenu`).
 */

import {
  type Connection,
  type EdgeChange,
  type IsValidConnection,
  type Node,
  type NodeChange,
  type Viewport,
  applyEdgeChanges,
  applyNodeChanges,
} from '@xyflow/react';
import { useCallback, useEffect, useMemo, useRef } from 'react';

import type { CanvasConnection, CanvasNode } from '../types';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { SMART_NODE_TYPES } from '../smart/nodes/registry';
import { CLASSIC_NODE_TYPES } from '../classic/ClassicNodeViews';
import { toReactFlowEdges, validateCanvasConnection } from './connectionMapping';
import { CanvasEngine } from '../../../canvas-kit/CanvasEngine';
import { DragCreateMenu } from './DragCreateMenu';

type AnyNode = Node;

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
  const setSelection = useCanvasCoreStore((s) => s.setSelection);
  const noteDragStart = useCanvasCoreStore((s) => s.noteDragStart);
  const setNodesDragTick = useCanvasCoreStore((s) => s.setNodesDragTick);
  const setNodesTransient = useCanvasCoreStore((s) => s.setNodesTransient);
  const flushHistory = useCanvasCoreStore((s) => s.flushHistory);
  const setViewportOnMove = useCanvasCoreStore((s) => s.setViewportOnMove);
  const flushViewportDirty = useCanvasCoreStore((s) => s.flushViewportDirty);

  // RAF handle for coalescing per-tick onMove dirty signals (Fix 2).
  const viewportRafRef = useRef<number | null>(null);

  // On unmount (e.g. rail view switch mid-drag) commit any in-flight history
  // base and cancel a pending viewport frame so nothing leaks into the next
  // mount of the singleton store.
  useEffect(() => {
    return () => {
      if (viewportRafRef.current !== null) {
        cancelAnimationFrame(viewportRafRef.current);
        viewportRafRef.current = null;
      }
      flushHistory();
    };
  }, [flushHistory]);

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

  // Fix 3 (6e-2c) — O(1) node-type lookup for connection validation.
  // `nodeTypeById` is called twice per connection event (source + target).
  // The Map rebuilds only when rfNodes rebuilds, so it is always consistent
  // with the rendered node list.
  const nodeTypeMap = useMemo(() => {
    const m = new Map<string, string>();
    for (const n of rfNodes) {
      if (n.type) m.set(n.id, n.type);
    }
    return m;
  }, [rfNodes]);
  const nodeTypeById = useCallback((id: string) => nodeTypeMap.get(id), [nodeTypeMap]);

  const onNodesChange = useCallback(
    (changes: NodeChange[]) => {
      const next = applyNodeChanges(changes, rfNodes);
      // Fix 1 — mid-drag ticks: ALL changes are position-type with dragging:true.
      // Route these through setNodesDragTick which skips the historyTimer reset,
      // cutting timer-reset churn from O(drag_ticks) to O(1) per drag gesture.
      const isMidDragOnly = changes.every(
        (c) => c.type === 'position' && (c as { dragging?: boolean }).dragging === true,
      );
      if (isMidDragOnly) {
        setNodesDragTick(next as unknown as CanvasNode[]);
        return;
      }
      // Split document edits from React Flow-internal changes. `select` (handled
      // separately via onSelectionChange) and `dimensions` (measurement) must
      // NOT create undo history or dirty/persist the document — otherwise a
      // mere click or the load-time measure pass saves the canvas and drops a
      // phantom undo step. Route those through the render-only transient path.
      const hasDocEdit = changes.some(
        (c) => c.type !== 'select' && c.type !== 'dimensions',
      );
      if (hasDocEdit) {
        setNodes(next as unknown as CanvasNode[]);
      } else {
        setNodesTransient(next as unknown as CanvasNode[]);
      }
    },
    [rfNodes, setNodes, setNodesDragTick, setNodesTransient],
  );

  const onEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      const next = applyEdgeChanges(changes, rfEdges);
      setConnections(next as unknown as CanvasConnection[]);
    },
    [rfEdges, setConnections],
  );

  // One-time solo-drop alignment snap committed through the store's setNodes
  // action so revision/persist stay consistent.
  const onNodesSnap = useCallback(
    (snapped: AnyNode[]) => {
      setNodes(snapped as unknown as CanvasNode[]);
    },
    [setNodes],
  );

  const onMove = useCallback(
    (nextViewport: Viewport) => {
      // Fix 2 — update viewport state immediately for React Flow controlled-mode
      // rendering (so panning feels instant), but coalesce the markDirty() +
      // revision-bump to at most once per animation frame via RAF.
      setViewportOnMove(nextViewport);
      if (viewportRafRef.current === null) {
        viewportRafRef.current = requestAnimationFrame(() => {
          viewportRafRef.current = null;
          flushViewportDirty();
        });
      }
    },
    [setViewportOnMove, flushViewportDirty],
  );

  const isConnectionValid = useCallback<IsValidConnection>(
    (connection) => validateCanvasConnection(connection, kind, nodeTypeById),
    [kind, nodeTypeById],
  );

  // Commit a dragged wire. We re-run the SAME validator as the live hint
  // (smart → canConnectSmart, classic → typed-port canConnectClassic). Valid
  // wires are appended to the store with a generated id + handle ids (the
  // cascade + multi-port validation address ports by handle, and
  // `toReactFlowEdges` round-trips them). Invalid wires are dropped silently.
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
    <CanvasEngine
      nodeTypes={nodeTypes}
      nodes={rfNodes}
      edges={rfEdges}
      selectedIds={selection}
      viewport={viewport}
      onNodesChange={onNodesChange}
      onNodeDragStart={noteDragStart}
      onNodesSnap={onNodesSnap}
      onEdgesChange={onEdgesChange}
      onMove={onMove}
      onSelectionChange={setSelection}
      allowConnect
      onConnect={onConnect}
      isValidConnection={isConnectionValid}
      allowDragCreate
      renderCreateMenu={(ctx, onClose) => (
        <DragCreateMenu
          screenPosition={ctx.screenPosition}
          flowPosition={ctx.flowPosition}
          fromNodeId={ctx.fromNodeId}
          fromHandle={ctx.fromHandle}
          onClose={onClose}
        />
      )}
    />
  );
}
