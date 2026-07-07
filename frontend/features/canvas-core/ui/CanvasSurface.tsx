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
  type ReactFlowInstance,
  type Viewport,
  applyEdgeChanges,
  applyNodeChanges,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { CanvasConnection, CanvasNode } from '../types';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { SMART_NODE_TYPES } from '../smart/nodes/registry';
import { CLASSIC_NODE_TYPES } from '../classic/ClassicNodeViews';
import { toReactFlowEdges, validateCanvasConnection } from './connectionMapping';
import {
  computeAlignmentGuides,
  type AlignmentGuides,
  type Rect,
} from '../../../canvas-kit/alignmentGuides';
import { GuideOverlay } from '../../../canvas-kit/GuideOverlay';
import { useCanvasShortcuts } from '../../../canvas-kit/useCanvasShortcuts';

type AnyNode = Node;

// MiniMap renders via SVG; CSS custom-properties are not available in that
// context, so we carry the hex values directly.  Both literals are derived
// from the ink palette dark-mode defaults (index.css § ink ladder):
//   INK_ACCENT → --color-accent  (#6366f1, indigo-500)   — selected nodes
//   INK_MUTED  → dark-mode --ink-600  (#52525b, zinc-600) — unselected nodes
const INK_ACCENT = '#6366f1';
const INK_MUTED = '#52525b';

/** Snap-to-grid step, px — the gentle 8px lattice shared with the script editor. */
const SNAP_GRID: [number, number] = [8, 8];
/** Stable empty-guides object so clearing never allocates a new render key. */
const NO_GUIDES: AlignmentGuides = {};
/** Node box used for guide math before React Flow has measured a node (flow px). */
const FALLBACK_NODE_WIDTH = 200;
const FALLBACK_NODE_HEIGHT = 120;

/**
 * Additive-selection modifier: Cmd on Apple platforms, Ctrl elsewhere — matches
 * the OS conventions React Flow's defaults follow. Shift is reserved for
 * box-select. `navigator.platform` is empty under jsdom, which resolves to Control.
 */
function detectMultiSelectKey(): 'Meta' | 'Control' {
  const platform = (typeof navigator !== 'undefined' && navigator.platform) || '';
  return /Mac|iPhone|iPad|iPod/i.test(platform) ? 'Meta' : 'Control';
}
const MULTI_SELECT_KEY = detectMultiSelectKey();

/** Bounding rect of a React Flow node in flow space, using measured size when known. */
function nodeRect(node: AnyNode): Rect {
  const measured = (node as { measured?: { width?: number; height?: number } }).measured;
  return {
    x: node.position.x,
    y: node.position.y,
    width: measured?.width ?? FALLBACK_NODE_WIDTH,
    height: measured?.height ?? FALLBACK_NODE_HEIGHT,
  };
}

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
  const noteDragStart = useCanvasCoreStore((s) => s.noteDragStart);
  const setNodesDragTick = useCanvasCoreStore((s) => s.setNodesDragTick);
  const setViewportOnMove = useCanvasCoreStore((s) => s.setViewportOnMove);
  const flushViewportDirty = useCanvasCoreStore((s) => s.flushViewportDirty);

  // RAF handle for coalescing per-tick onMove dirty signals (Fix 2).
  const viewportRafRef = useRef<number | null>(null);

  // Focusable host for the canvas-kit keyboard layer + the live React Flow
  // instance it drives (fit-view / zoom).
  const containerRef = useRef<HTMLDivElement>(null);
  const instanceRef = useRef<ReactFlowInstance | null>(null);
  // Alignment-guide lines drawn while a single node drags.
  const [guides, setGuides] = useState<AlignmentGuides>(NO_GUIDES);

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

  // Latest rendered nodes, read synchronously by the drag handlers to compute
  // alignment guides against the other nodes without re-binding on every tick.
  const rfNodesRef = useRef(rfNodes);
  useEffect(() => {
    rfNodesRef.current = rfNodes;
  }, [rfNodes]);

  // Fix 3 (6e-2c) — O(1) node-type lookup for connection validation.
  // `nodeTypeById` is called twice per connection event (source + target).
  // Previously: rfNodes.find(n => n.id === id) = O(N) per call.
  // Now: Map<id, type> built once alongside rfNodes, O(1) per call.
  // The Map rebuilds only when rfNodes rebuilds (same [rfNodes] dep),
  // so it is always consistent with the rendered node list.
  const nodeTypeMap = useMemo(() => {
    const m = new Map<string, string>();
    for (const n of rfNodes) {
      if (n.type) m.set(n.id, n.type);
    }
    return m;
  }, [rfNodes]);

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
      } else {
        setNodes(next as unknown as CanvasNode[]);
      }
    },
    [rfNodes, setNodes, setNodesDragTick],
  );

  // Fix 1 — capture pre-drag snapshot exactly once, before the first tick.
  const onNodeDragStart = useCallback(() => {
    noteDragStart();
  }, [noteDragStart]);

  // While a single node drags, match its edges against the others and draw the
  // alignment guides. The snap itself is applied once on drop so the node never
  // fights the cursor mid-drag.
  const onNodeDrag = useCallback((_evt: unknown, node: AnyNode) => {
    const others = rfNodesRef.current.filter((n) => n.id !== node.id);
    setGuides(computeAlignmentGuides(nodeRect(node), others.map(nodeRect)));
  }, []);

  const onNodeDragStop = useCallback(
    (_evt: unknown, node: AnyNode) => {
      setGuides(NO_GUIDES);
      // Group drop: React Flow moved the whole selection and every position
      // already flowed through onNodesChange → setNodes (the store's persist
      // channel), so skip the single-node snap rather than fight it.
      const selection = useCanvasCoreStore.getState().selection;
      if (selection.length > 1 && selection.includes(node.id)) return;
      // Solo drop: apply a one-time alignment snap (wins over the 8px grid),
      // committed through the store action so revision/persist stay consistent.
      const others = rfNodesRef.current.filter((n) => n.id !== node.id);
      const g = computeAlignmentGuides(nodeRect(node), others.map(nodeRect));
      if (g.snappedX == null && g.snappedY == null) return;
      const snapped = rfNodesRef.current.map((n) =>
        n.id === node.id
          ? {
              ...n,
              position: {
                x: g.snappedX ?? n.position.x,
                y: g.snappedY ?? n.position.y,
              },
            }
          : n,
      );
      setNodes(snapped as unknown as CanvasNode[]);
    },
    [setNodes],
  );

  // Adopt the shared canvas-kit keyboard layer for the two shortcuts canvas-core
  // does NOT already own: fit-view (f) and zoom (+/-). Escape and Cmd/Ctrl+A are
  // deliberately no-ops here — the window-scoped useCanvasShortcuts (CanvasPage)
  // already owns them, so re-binding would double-handle.
  useCanvasShortcuts(containerRef, {
    onZoomIn: () => instanceRef.current?.zoomIn(),
    onZoomOut: () => instanceRef.current?.zoomOut(),
    onFitView: () => instanceRef.current?.fitView(),
    onSelectAll: () => {},
    onClearSelection: () => {},
  });

  const onEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      const next = applyEdgeChanges(changes, rfEdges);
      setConnections(next as unknown as CanvasConnection[]);
    },
    [rfEdges, setConnections],
  );

  const onMove = useCallback(
    (_event: unknown, nextViewport: Viewport) => {
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
  // Fix 3 (6e-2c): use O(1) Map instead of O(N) rfNodes.find(…).
  const nodeTypeById = useCallback(
    (id: string) => nodeTypeMap.get(id),
    [nodeTypeMap],
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
    <div ref={containerRef} tabIndex={0} className="relative h-full w-full outline-none">
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        onInit={(instance) => {
          instanceRef.current = instance;
        }}
        onNodesChange={onNodesChange}
        onNodeDragStart={onNodeDragStart}
        onNodeDrag={onNodeDrag}
        onNodeDragStop={onNodeDragStop}
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
        // Gentle 8px snap lattice + one-time alignment snap on solo drop.
        snapGrid={SNAP_GRID}
        snapToGrid
        // Shift-drag for box select; Cmd/Ctrl adds to the selection; click-drag
        // pans the viewport, matching Figma / Miro / Excalidraw.
        selectionMode={SelectionMode.Partial}
        selectionOnDrag={false}
        selectionKeyCode="Shift"
        multiSelectionKeyCode={MULTI_SELECT_KEY}
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
        <GuideOverlay guides={guides} />
      </ReactFlow>
    </div>
  );
}
