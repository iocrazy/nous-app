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
  type Edge,
  type EdgeChange,
  type IsValidConnection,
  type Node,
  type NodeChange,
  type Viewport,
  applyEdgeChanges,
  applyNodeChanges,
} from '@xyflow/react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { CanvasConnection, CanvasNode } from '../types';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { SMART_NODE_TYPES } from '../smart/nodes/registry';
import { SMART_EDGE_TYPES } from '../smart/edges/registry';
import { CLASSIC_NODE_TYPES } from '../classic/ClassicNodeViews';
import { toReactFlowEdges, validateCanvasConnection } from './connectionMapping';
import { edgeRunStateClass } from '../smart/edgeRunState';
import { CanvasEngine } from '../../../canvas-kit/CanvasEngine';
import { KnifeOverlay } from '../../../canvas-kit/KnifeOverlay';
import { sampleEdgesFromDom, sampleNodesFromDom } from '../../../canvas-kit/knifeDomSampling';
import { useKnifeStore } from '../../../canvas-kit/knifeStore';
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
    // xyflow v12 contract: controlled flows must echo the `measured` field
    // applyNodeChanges writes back into the nodes prop — NodeWrapper keeps a
    // node `visibility: hidden` until it has dimensions, so stripping this
    // left every node permanently invisible in a real browser.
    const measured =
      obj.measured && typeof obj.measured === 'object'
        ? { measured: obj.measured as { width?: number; height?: number } }
        : {};
    return { id, position, type, data, ...measured } as AnyNode;
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
  // Stable signature of non-idle prompt run statuses. Drag ticks swap the
  // nodes array identity every frame; folding the statuses into a string
  // means the edge-decoration memo below only re-runs when a status truly
  // changes — edge identities stay stable mid-drag (RF re-renders every
  // EdgeWrapper when they churn). Empty string = nothing to decorate.
  const promptStatusSig = useMemo(() => {
    if (kind !== 'smart') return '';
    const entries: Array<[string, string]> = [];
    for (const n of nodes) {
      const obj = n as Record<string, unknown>;
      if (obj.type !== 'prompt') continue;
      const data = (obj.data ?? {}) as { run_status?: string };
      if (data.run_status && data.run_status !== 'idle') {
        entries.push([String(obj.id), data.run_status]);
      }
    }
    return entries.length ? JSON.stringify(entries) : '';
  }, [kind, nodes]);

  // Edge selection is VIEW-ONLY state (G6 scissors needs `selected` on the
  // edge component). It lives here, not in the store: connections_json must
  // never carry it, and a mere click must not dirty/persist the document.
  const [selectedEdgeIds, setSelectedEdgeIds] = useState<ReadonlySet<string>>(
    () => new Set(),
  );

  // Run-state edge colouring (Infinite parity G2, smart only): edges carry
  // the adjacent prompt's run_status as a class so a cascade run visibly
  // flows wait → active → done along the wires (index.css § Canvas chrome).
  const rfEdges = useMemo(() => {
    const edges = toReactFlowEdges(connections).map((edge) =>
      selectedEdgeIds.has(edge.id) ? { ...edge, selected: true } : edge,
    );
    if (kind !== 'smart' || !promptStatusSig) return edges;
    const statusById = new Map(JSON.parse(promptStatusSig) as Array<[string, string]>);
    return edges.map((edge) => {
      const cls = edgeRunStateClass(edge, (id) => {
        const runStatus = statusById.get(id);
        return runStatus ? { type: 'prompt', runStatus } : undefined;
      });
      return cls ? { ...edge, className: cls } : edge;
    });
  }, [connections, kind, promptStatusSig, selectedEdgeIds]);

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
      // Apply against the store's CURRENT nodes, not the render-closure
      // rfNodes snapshot: a click on a button inside an unselected node
      // fires patchNode and RF's select change in the SAME tick — merging
      // the select change into the stale snapshot would silently roll the
      // patch back (found by the G8 timeline e2e; Retry chips had the same
      // exposure). rfNodes only adds view-only selected/className derivations
      // on top of the store, so the change math is identical.
      // Node `select` changes route into the store's selection ARRAY —
      // rfNodes derives `selected` from it, so writing a selected field
      // onto store nodes (the old path) never fed back to React Flow and
      // clicking a node could never select it (the long-standing
      // "composer Run stays disabled" backlog bug).
      const selects = changes.filter((c) => c.type === 'select');
      if (selects.length > 0) {
        const store = useCanvasCoreStore.getState();
        const sel = new Set(store.selection);
        for (const c of selects) {
          const change = c as { id: string; selected: boolean };
          if (change.selected) sel.add(change.id);
          else sel.delete(change.id);
        }
        store.setSelection([...sel]);
      }
      const rest = changes.filter((c) => c.type !== 'select');
      if (rest.length === 0) return;
      const current = useCanvasCoreStore.getState()
        .nodes as unknown as typeof rfNodes;
      const next = applyNodeChanges(rest, current);
      // Fix 1 — mid-drag ticks: ALL changes are position-type with dragging:true.
      // Route these through setNodesDragTick which skips the historyTimer reset,
      // cutting timer-reset churn from O(drag_ticks) to O(1) per drag gesture.
      const isMidDragOnly = rest.every(
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
      const hasDocEdit = rest.some((c) => c.type !== 'dimensions');
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
      // `select` changes only touch the view-only selection set — they never
      // reach the store, so clicking a wire cannot dirty the document.
      const selects = changes.filter((c) => c.type === 'select');
      if (selects.length) {
        setSelectedEdgeIds((prev) => {
          const next = new Set(prev);
          for (const c of selects) {
            if ((c as { selected: boolean }).selected) next.add(c.id);
            else next.delete(c.id);
          }
          return next;
        });
      }
      const docChanges = changes.filter((c) => c.type !== 'select');
      if (docChanges.length === 0) return;
      // Strip view-only fields before the store: the run-state className
      // (and RF's selected flag) must never reach connections_json — stale
      // decoration in the DB row makes collaborators' saves diverge
      // byte-wise and trips the false-realtime-conflict path.
      const next = applyEdgeChanges(docChanges, rfEdges).map((edge) => {
        const { className: _className, selected: _selected, ...rest } = edge as Edge & {
          className?: string;
        };
        return rest;
      });
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
      const sourceHandle = connection.sourceHandle ?? null;
      const targetHandle = connection.targetHandle ?? null;
      // Skip a duplicate of an existing edge (same endpoints + handles) so a
      // second identical connect gesture doesn't stack a redundant edge.
      const isDuplicate = connections.some(
        (c) =>
          c.source === connection.source &&
          c.target === connection.target &&
          (c.sourceHandle ?? null) === sourceHandle &&
          (c.targetHandle ?? null) === targetHandle,
      );
      if (isDuplicate) return;
      const newEdge: CanvasConnection = {
        id: `edge-${crypto.randomUUID()}`,
        source: connection.source,
        target: connection.target,
        sourceHandle,
        targetHandle,
      };
      setConnections([...connections, newEdge]);
    },
    [kind, nodeTypeById, connections, setConnections],
  );

  // Drag-snap-connect (Infinite-Canvas parity G1, smart only): a ctrl-dropped
  // node becomes the SOURCE of a new edge into the hovered target, then snaps
  // back to where the drag started — the gesture wires, it never moves.
  // Prompt/loop sources probe with the pointer (their bodies are large and
  // text-heavy); everything else probes with the node center, per Infinite.
  const snapProbeFor = useCallback(
    (node: AnyNode) =>
      node.type === 'prompt' || node.type === 'loop' ? ('pointer' as const) : ('center' as const),
    [],
  );

  const onSnapConnect = useCallback(
    (args: { source: AnyNode; target: AnyNode; dragStartPosition: { x: number; y: number } }) => {
      onConnect({
        source: args.source.id,
        target: args.target.id,
        sourceHandle: null,
        targetHandle: null,
      });
      const current = useCanvasCoreStore.getState().nodes;
      setNodes(
        current.map((n) =>
          (n as { id?: unknown }).id === args.source.id
            ? { ...n, position: { ...args.dragStartPosition } }
            : n,
        ) as CanvasNode[],
      );
    },
    [onConnect, setNodes],
  );

  const knifeActive = useKnifeStore((s) => s.active);
  const exitKnife = useKnifeStore((s) => s.exit);
  const onKnifeCut = useCallback(
    (edgeIds: string[]) => {
      const cut = new Set(edgeIds);
      const store = useCanvasCoreStore.getState();
      store.setConnections(
        store.connections.filter(
          (c) => !cut.has(String((c as Record<string, unknown>).id)),
        ),
      );
      // The cut edges may have been selected — drop their view-only flags.
      setSelectedEdgeIds((prev) => {
        const next = new Set(prev);
        for (const id of cut) next.delete(id);
        return next;
      });
    },
    [],
  );

  const nodeTypes =
    kind === 'smart'
      ? SMART_NODE_TYPES
      : kind === 'classic'
        ? CLASSIC_NODE_TYPES
        : undefined;
  // Smart mode swaps the default edge for the scissors edge (G6 conn-cut);
  // classic keeps the stock bezier so nothing else changes.
  const edgeTypes = kind === 'smart' ? SMART_EDGE_TYPES : undefined;

  return (
    <CanvasEngine
      themedChrome
      // Infinite-parity zoom range (P0-6): RF's default 0.5–2 clamp feels
      // "stuck" next to Infinite's effectively unbounded zoom, and capped
      // the `z` overview on large graphs.
      minZoom={0.1}
      maxZoom={4}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
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
      allowSnapConnect={kind === 'smart'}
      snapProbeFor={snapProbeFor}
      onSnapConnect={onSnapConnect}
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
      renderOverlay={(container) =>
        knifeActive && container ? (
          <KnifeOverlay
            sampleEdges={() => sampleEdgesFromDom(container)}
            sampleNodes={() => sampleNodesFromDom(container)}
            edgeEndpoints={() => {
              const map: Record<string, { source: string; target: string }> = {};
              for (const c of useCanvasCoreStore.getState().connections) {
                const obj = c as Record<string, unknown>;
                map[String(obj.id)] = {
                  source: String(obj.source),
                  target: String(obj.target),
                };
              }
              return map;
            }}
            onCut={onKnifeCut}
            onExit={exitKnife}
          />
        ) : null
      }
    />
  );
}
