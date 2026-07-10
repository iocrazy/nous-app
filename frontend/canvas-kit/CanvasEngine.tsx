/**
 * CanvasEngine — the single ReactFlow assembly every canvas surface runs on
 * (canvas-kit). Extracted from canvas-core's CanvasSurface so the classic /
 * smart / scene consumers share one engine + one feel.
 *
 * The engine owns everything a canvas always wants: the ReactFlow shell with
 * Background / Controls / MiniMap, box-select + snap-grid + platform additive
 * key, the alignment-guide overlay, fit-view / zoom shortcuts, and magnetic
 * drag-to-create. What is *canvas data semantics* — how a node maps to a store
 * row, how a wire is validated, which persist channel a drag routes through —
 * stays with the caller, injected through controlled props + change callbacks.
 *
 * Capability toggles keep read-only surfaces honest: `allowConnect={false}`
 * detaches every connection handler (scene projections stay uneditable);
 * `allowDragCreate={false}` detaches the wire-drag-to-create layer.
 *
 * Chrome (MiniMap colouring / mask / a11y label, Controls placement) and the
 * fallback node box used for guide math differ per canvas, so they are config
 * props with canvas-core-compatible defaults — a consumer that omits them gets
 * exactly the classic/smart surface.
 */

import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  SelectionMode,
  type Connection,
  type Edge,
  type EdgeChange,
  type IsValidConnection,
  type Node,
  type NodeChange,
  type NodeMouseHandler,
  type NodeTypes,
  type OnSelectionChangeParams,
  type PanelPosition,
  type ReactFlowInstance,
  type Viewport,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

import {
  computeAlignmentGuides,
  type AlignmentGuides,
  type Rect,
} from './alignmentGuides';
import { GuideOverlay } from './GuideOverlay';
import { snapConnectTargetFor } from './snapConnect';
import { useCanvasShortcuts } from './useCanvasShortcuts';
import { useDragToCreate } from './useDragToCreate';
import type { SnapPort } from './portSnap';

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

/** Alignment snap tolerance, in SCREEN pixels (divided by zoom at use). */
const GUIDE_TOLERANCE_SCREEN_PX = 5;
/** Node box used for guide math before React Flow has measured a node (flow px). */
const FALLBACK_NODE_WIDTH = 200;
const FALLBACK_NODE_HEIGHT = 120;

/** Default node box: measured size when React Flow knows it, else the classic fallback. */
function defaultNodeMeasure(node: AnyNode): { width: number; height: number } {
  const measured = (node as { measured?: { width?: number; height?: number } }).measured;
  return {
    width: measured?.width ?? FALLBACK_NODE_WIDTH,
    height: measured?.height ?? FALLBACK_NODE_HEIGHT,
  };
}

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

/**
 * Context handed to the caller when a wire is dropped into empty canvas: the
 * flow-space drop point (where a spawned node should land), a container-relative
 * screen point to anchor a picker at, and the origin handle to auto-wire from.
 */
export interface CreateMenuContext {
  screenPosition: { x: number; y: number };
  flowPosition: { x: number; y: number };
  fromNodeId: string;
  fromHandle: string | null;
}

/** What the engine computed on a node drag-stop, so the caller can persist it. */
export interface NodeDragStopContext {
  /** The dropped node was part of an active multi-selection (group move). */
  isGroupDrop: boolean;
  /** Alignment-snap target for a solo drop, or null when nothing snapped. */
  snappedPosition: { x: number; y: number } | null;
  /** The full rendered node list (snapshot at drop). */
  nodes: AnyNode[];
}

/** MiniMap config — omit for the classic/smart selection-tinted map. */
export interface MinimapConfig {
  position?: PanelPosition;
  pannable?: boolean;
  zoomable?: boolean;
  nodeColor?: (node: AnyNode) => string;
  nodeStrokeWidth?: number;
  maskColor?: string;
  ariaLabel?: string;
}

/** Controls config — omit for the classic/smart bottom-right control cluster. */
export interface ControlsConfig {
  position?: PanelPosition;
  showInteractive?: boolean;
}

export interface CanvasEngineProps {
  /**
   * Opt-in Infinite-Canvas-parity chrome (Phase 0 G10): adds the `mh-canvas`
   * scope class (index.css § Canvas chrome — page/grid/edge/handle/minimap
   * tokens) and the 24px dot lattice. OFF by default so surfaces with their
   * own theming (editor NodesView inside `.mh-editor-shell`) keep their look.
   */
  themedChrome?: boolean;
  /** Node component registry for the current consumer (classic / smart / scene). */
  nodeTypes?: NodeTypes;
  /** Controlled React Flow nodes (already carrying `selected`). */
  nodes: AnyNode[];
  /** Controlled React Flow edges. */
  edges: Edge[];
  /** Selected node ids — drives the default (selection-tinted) MiniMap colour. */
  selectedIds?: string[];
  /** Controlled viewport; omit for an uncontrolled surface (e.g. with fitView). */
  viewport?: Viewport;
  /** Fit the graph into view on mount. */
  fitView?: boolean;
  /** Node box for guide + snap math; defaults to measured-size-or-200×120. */
  nodeMeasure?: (node: AnyNode) => { width: number; height: number };

  /** Raw React Flow node changes (caller applies + routes to its persist channel). */
  onNodesChange: (changes: NodeChange[]) => void;
  /** Raw React Flow edge changes. */
  onEdgesChange: (changes: EdgeChange[]) => void;
  /** Fired once at the start of a node drag (pre-drag snapshot for history). */
  onNodeDragStart?: () => void;
  /**
   * Convenience solo-snap commit: the full node list with the dropped node's
   * position corrected to the guide. Fires only when a solo drop snapped.
   */
  onNodesSnap?: (nodes: AnyNode[]) => void;
  /**
   * Full drag-stop hook (guides already cleared): the dropped node plus what the
   * engine computed (group vs. solo, snap target). The caller owns persistence.
   */
  onNodeDragStop?: (node: AnyNode, ctx: NodeDragStopContext) => void;
  /** Multi-selection box-drag settled — the dragged nodes (guides already cleared). */
  onSelectionDragStop?: (nodes: AnyNode[]) => void;
  /** Viewport moved (caller owns any dirty-coalescing). */
  onMove?: (viewport: Viewport) => void;
  /** Selection changed — node ids in React Flow's selection order. */
  onSelectionChange?: (ids: string[]) => void;
  /** Double-click on a node (scene mode opens the chapter; others may ignore). */
  onNodeDoubleClick?: NodeMouseHandler;
  /** Cmd/Ctrl+A while the surface is focused (default: no-op). */
  onSelectAll?: () => void;
  /** Escape while the surface is focused (default: no-op). */
  onClearSelection?: () => void;

  /** When false (default true) no connection handler is attached — read-only wires. */
  allowConnect?: boolean;
  /** Commit a dragged wire (caller validates). */
  onConnect?: (connection: Connection) => void;
  /** Live drag-validity hint. */
  isValidConnection?: IsValidConnection;

  /**
   * Alt-dragging a single node so its probe point lands inside a valid
   * target offers an automatic connection (Infinite-Canvas parity G1).
   * Infinite gates on Ctrl, but d3-drag's default filter swallows
   * ctrl+mousedown and ReactFlow suppresses node drags while the Shift
   * selection key is held — Alt is the only modifier that starts a node
   * drag cleanly. (G6 duplicate-drag will arbitrate gestures when it lands.)
   * The engine highlights the hovered target (`mh-snap-target`) and fires
   * `onSnapConnect` on drop; the caller owns edge creation + snap-back.
   */
  allowSnapConnect?: boolean;
  /** Probe used for the hit test: node center (default) or the pointer. */
  snapProbeFor?: (node: AnyNode) => 'center' | 'pointer';
  /** An alt-drop landed on a valid target — create the edge, restore the node. */
  onSnapConnect?: (args: {
    source: AnyNode;
    target: AnyNode;
    dragStartPosition: { x: number; y: number };
  }) => void;

  /** When true, wire-drag off a source handle snaps to a port or opens a picker. */
  allowDragCreate?: boolean;
  /** Candidate target ports in flow space; defaults to every measured target handle. */
  getPorts?: () => SnapPort[];
  /** Render the caller's node picker when a wire drops into empty canvas. */
  renderCreateMenu?: (ctx: CreateMenuContext, onClose: () => void) => ReactNode;

  /** MiniMap chrome overrides. */
  minimap?: MinimapConfig;
  /** Controls chrome overrides. */
  controls?: ControlsConfig;
}

const NOOP = () => {};

export function CanvasEngine({
  themedChrome = false,
  nodeTypes,
  nodes,
  edges,
  selectedIds,
  viewport,
  fitView = false,
  nodeMeasure,
  onNodesChange,
  onEdgesChange,
  onNodeDragStart,
  onNodesSnap,
  onNodeDragStop,
  onSelectionDragStop,
  onMove,
  onSelectionChange,
  onNodeDoubleClick,
  onSelectAll = NOOP,
  onClearSelection = NOOP,
  allowConnect = true,
  onConnect,
  isValidConnection,
  allowSnapConnect = false,
  snapProbeFor,
  onSnapConnect,
  allowDragCreate = false,
  getPorts,
  renderCreateMenu,
  minimap,
  controls,
}: CanvasEngineProps) {
  // Focusable host for the keyboard layer + the live React Flow instance it
  // drives (fit-view / zoom / port measurement).
  const containerRef = useRef<HTMLDivElement>(null);
  const instanceRef = useRef<ReactFlowInstance | null>(null);
  // Alignment-guide lines drawn while a single node drags.
  const [guides, setGuides] = useState<AlignmentGuides>(NO_GUIDES);
  // Drag-to-create menu: open at a container-relative screen point, carrying the
  // flow position + origin handle so a picked node lands + auto-wires correctly.
  const [createMenu, setCreateMenu] = useState<CreateMenuContext | null>(null);

  // Bounding rect of a node in flow space via the (possibly injected) measurer.
  const toRect = useCallback(
    (node: AnyNode): Rect => {
      const { width, height } = (nodeMeasure ?? defaultNodeMeasure)(node);
      return { x: node.position.x, y: node.position.y, width, height };
    },
    [nodeMeasure],
  );

  // Default MiniMap tint: selected nodes get the app accent, others get muted.
  const selectionSet = useMemo(() => new Set(selectedIds ?? []), [selectedIds]);
  const defaultNodeColor = useCallback(
    (n: AnyNode) => (selectionSet.has(n.id) ? INK_ACCENT : INK_MUTED),
    [selectionSet],
  );

  // Latest rendered nodes, read synchronously by the drag handlers (guides +
  // group-drop detection) without re-binding on every position change.
  const rfNodesRef = useRef(nodes);
  useEffect(() => {
    rfNodesRef.current = nodes;
  }, [nodes]);

  // Drag-start position of the current solo drag — snap-connect restores the
  // node here (the gesture creates a wire, it must not move the node).
  const dragStartPosRef = useRef<{ x: number; y: number } | null>(null);
  // Hovered snap-connect target (drives the `mh-snap-target` highlight).
  const [snapTargetId, setSnapTargetId] = useState<string | null>(null);

  const handleNodeDragStart = useCallback(
    (_evt: unknown, node?: AnyNode) => {
      dragStartPosRef.current = node ? { ...node.position } : null;
      onNodeDragStart?.();
    },
    [onNodeDragStart],
  );

  // Snap-connect hit test (Infinite parity): Alt held, solo drag, probe point
  // (node center; pointer for prompt/loop-style sources) inside a candidate
  // box, pair accepted by the caller's connection validator.
  const snapConnectTarget = useCallback(
    (evt: unknown, node: AnyNode): AnyNode | null => {
      if (!allowSnapConnect || !onSnapConnect) return null;
      const e = evt as { altKey?: boolean; clientX?: number; clientY?: number } | null;
      if (!e?.altKey) return null;
      const selected = rfNodesRef.current.filter((n) => n.selected);
      if (selected.length > 1 && selected.some((n) => n.id === node.id)) return null;

      let probePoint: { x: number; y: number };
      if (snapProbeFor?.(node) === 'pointer' && e.clientX != null && e.clientY != null) {
        const client = { x: e.clientX, y: e.clientY };
        // snapToGrid:false — the probe is an exact hit test; the flow's 8px
        // lattice would quantize it by up to ±4px at target edges.
        probePoint =
          instanceRef.current?.screenToFlowPosition?.(client, { snapToGrid: false }) ?? client;
      } else {
        const r = toRect(node);
        probePoint = { x: r.x + r.width / 2, y: r.y + r.height / 2 };
      }

      const hit = snapConnectTargetFor({
        draggedId: node.id,
        probePoint,
        candidates: rfNodesRef.current.map((n) => ({ id: n.id, rect: toRect(n) })),
      });
      if (!hit) return null;
      const valid =
        isValidConnection?.({
          source: node.id,
          target: hit.id,
          sourceHandle: null,
          targetHandle: null,
        }) !== false;
      if (!valid) return null;
      return rfNodesRef.current.find((n) => n.id === hit.id) ?? null;
    },
    [allowSnapConnect, onSnapConnect, snapProbeFor, isValidConnection, toRect],
  );

  // Alignment tolerance is a constant on SCREEN, so divide the flow-space
  // tolerance by the live zoom — otherwise a fixed flow px value snaps too
  // eagerly when zoomed in and never snaps when zoomed out.
  const guideTolerance = useCallback(() => {
    const zoom = instanceRef.current?.getViewport?.().zoom ?? viewport?.zoom ?? 1;
    return GUIDE_TOLERANCE_SCREEN_PX / (zoom > 0 ? zoom : 1);
  }, [viewport]);

  // While a single node drags, match its edges against the others and draw the
  // alignment guides. The snap itself is applied once on drop so the node never
  // fights the cursor mid-drag.
  const onNodeDrag = useCallback(
    (evt: unknown, node: AnyNode) => {
      const others = rfNodesRef.current.filter((n) => n.id !== node.id);
      setGuides(
        computeAlignmentGuides(toRect(node), others.map(toRect), guideTolerance()),
      );
      setSnapTargetId(snapConnectTarget(evt, node)?.id ?? null);
    },
    [toRect, guideTolerance, snapConnectTarget],
  );

  const handleNodeDragStop = useCallback(
    (evt: unknown, node: AnyNode) => {
      setGuides(NO_GUIDES);
      setSnapTargetId(null);
      // Snap-connect drop: the caller creates the edge and restores the node's
      // position — the alignment-snap commit below must not fight that.
      const snapTarget = snapConnectTarget(evt, node);
      if (snapTarget) {
        onSnapConnect?.({
          source: node,
          target: snapTarget,
          dragStartPosition: dragStartPosRef.current ?? { ...node.position },
        });
        onNodeDragStop?.(node, {
          isGroupDrop: false,
          snappedPosition: null,
          nodes: rfNodesRef.current,
        });
        return;
      }
      // Group drop: React Flow moved the whole selection; every position already
      // flowed through onNodesChange, so the engine computes no solo snap here.
      const selected = rfNodesRef.current.filter((n) => n.selected);
      const isGroupDrop = selected.length > 1 && selected.some((n) => n.id === node.id);
      let snappedPosition: { x: number; y: number } | null = null;
      if (!isGroupDrop) {
        const others = rfNodesRef.current.filter((n) => n.id !== node.id);
        const g = computeAlignmentGuides(
          toRect(node),
          others.map(toRect),
          guideTolerance(),
        );
        if (g.snappedX != null || g.snappedY != null) {
          snappedPosition = {
            x: g.snappedX ?? node.position.x,
            y: g.snappedY ?? node.position.y,
          };
          // Convenience path (classic/smart): commit the snap through the store.
          if (onNodesSnap) {
            const target = snappedPosition;
            onNodesSnap(
              rfNodesRef.current.map((n) =>
                n.id === node.id ? { ...n, position: target } : n,
              ),
            );
          }
        }
      }
      // Full path (scene): hand the caller everything to persist as it sees fit.
      onNodeDragStop?.(node, { isGroupDrop, snappedPosition, nodes: rfNodesRef.current });
    },
    [toRect, onNodesSnap, onNodeDragStop, guideTolerance, snapConnectTarget, onSnapConnect],
  );

  const handleSelectionDragStop = useCallback(
    (_evt: unknown, dragged: AnyNode[]) => {
      setGuides(NO_GUIDES);
      onSelectionDragStop?.(dragged);
    },
    [onSelectionDragStop],
  );

  // Adopt the shared canvas-kit keyboard layer for fit-view (f) and zoom (+/-).
  // Select-all / clear default to no-ops — consumers whose window-scoped layer
  // already owns them (canvas-core's CanvasPage) leave these unset so we never
  // double-handle.
  useCanvasShortcuts(containerRef, {
    onZoomIn: () => instanceRef.current?.zoomIn(),
    onZoomOut: () => instanceRef.current?.zoomOut(),
    onFitView: () => instanceRef.current?.fitView(),
    onSelectAll,
    onClearSelection,
  });

  const handleMove = useCallback(
    (_event: unknown, nextViewport: Viewport) => {
      onMove?.(nextViewport);
    },
    [onMove],
  );

  const handleSelectionChange = useCallback(
    (params: OnSelectionChangeParams) => {
      onSelectionChange?.(params.nodes.map((n) => n.id));
    },
    [onSelectionChange],
  );

  // Default flow-space target ports of every node, measured by React Flow. Used
  // by the magnetic snap; empty until the instance has measured handles (e.g.
  // jsdom), in which case a wire-drop simply falls through to the create menu.
  const collectTargetPorts = useCallback((): SnapPort[] => {
    const inst = instanceRef.current;
    if (!inst) return [];
    const ports: SnapPort[] = [];
    for (const n of inst.getNodes()) {
      const internal = inst.getInternalNode?.(n.id);
      const bounds = internal?.internals?.handleBounds?.target;
      if (!bounds) continue;
      for (const h of bounds) {
        ports.push({
          // Keep null for unnamed handles so it matches the create-menu path
          // (which auto-wires with targetHandle: null); '' vs null differ to
          // React Flow's handle matching.
          id: h.id ?? null,
          nodeId: n.id,
          x: n.position.x + h.x + h.width / 2,
          y: n.position.y + h.y + h.height / 2,
        });
      }
    }
    return ports;
  }, []);

  // Drag a wire off a source handle → magnetic snap to a nearby port (routed
  // through the caller's onConnect), or open the create menu on empty canvas.
  const dragToCreate = useDragToCreate({
    getPorts: getPorts ?? collectTargetPorts,
    // Release point comes in as viewport SCREEN coords; convert to flow space.
    toFlowPosition: (screenPoint) =>
      instanceRef.current?.screenToFlowPosition(screenPoint) ?? screenPoint,
    // A magnetic snap must respect the same legality rule as a manual connect;
    // an illegal snap falls through to the create menu (handled in the hook).
    isValidTarget: ({ fromNodeId, fromHandle, toNodeId, toHandle }) =>
      isValidConnection
        ? isValidConnection({
            source: fromNodeId,
            target: toNodeId,
            sourceHandle: fromHandle,
            targetHandle: toHandle,
          })
        : true,
    onMagneticConnect: ({ fromNodeId, fromHandle, toNodeId, toHandle }) => {
      // Read-only surfaces (allowConnect=false) must never gain an edge, even
      // if a consumer opts into drag-create — the two switches are orthogonal.
      if (!allowConnect) return;
      onConnect?.({
        source: fromNodeId,
        target: toNodeId,
        sourceHandle: fromHandle,
        targetHandle: toHandle,
      });
    },
    onOpenCreateMenu: ({ flowPosition, fromNodeId, fromHandle }) => {
      const inst = instanceRef.current;
      const rect = containerRef.current?.getBoundingClientRect();
      let screenPosition = { x: 0, y: 0 };
      if (inst && rect) {
        const client = inst.flowToScreenPosition(flowPosition);
        screenPosition = { x: client.x - rect.left, y: client.y - rect.top };
      }
      setCreateMenu({ screenPosition, flowPosition, fromNodeId, fromHandle });
    },
  });

  // Hovered snap-connect target gets the dashed `mh-snap-target` outline
  // (index.css § Canvas chrome). Identity when nothing is hovered.
  const displayNodes = useMemo(() => {
    if (!snapTargetId) return nodes;
    return nodes.map((n) =>
      n.id === snapTargetId
        ? { ...n, className: `${n.className ?? ''} mh-snap-target`.trim() }
        : n,
    );
  }, [nodes, snapTargetId]);

  return (
    // `mh-canvas` scopes the Infinite-Canvas-parity chrome (grid/edge/handle/
    // minimap tokens, index.css § Canvas chrome) to opted-in surfaces only —
    // the editor NodesView keeps its own `.mh-editor-shell` theming.
    <div
      ref={containerRef}
      tabIndex={0}
      className={`${themedChrome ? 'mh-canvas ' : ''}relative h-full w-full outline-none`}
    >
      <ReactFlow
        nodes={displayNodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onInit={(instance) => {
          instanceRef.current = instance;
        }}
        onNodesChange={onNodesChange}
        onNodeDragStart={handleNodeDragStart}
        onNodeDrag={onNodeDrag}
        onNodeDragStop={handleNodeDragStop}
        onSelectionDragStop={onSelectionDragStop ? handleSelectionDragStop : undefined}
        onNodeDoubleClick={onNodeDoubleClick}
        onEdgesChange={onEdgesChange}
        onConnect={allowConnect ? onConnect : undefined}
        onConnectStart={allowDragCreate ? dragToCreate.onConnectStart : undefined}
        onConnectEnd={allowDragCreate ? dragToCreate.onConnectEnd : undefined}
        onMove={handleMove}
        onSelectionChange={handleSelectionChange}
        isValidConnection={allowConnect ? isValidConnection : undefined}
        viewport={viewport}
        fitView={fitView}
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
        {/* 24px dot lattice per Infinite-Canvas (`radial-gradient … 24px`)
            when the parity chrome is on; dot color comes from --canvas-grid
            via the .mh-canvas override. Legacy surfaces keep the 20px grid. */}
        <Background variant={BackgroundVariant.Dots} gap={themedChrome ? 24 : 20} size={1} />
        <Controls
          position={controls?.position ?? 'bottom-right'}
          showInteractive={controls?.showInteractive}
        />
        {/* 6b.2 — minimap: pannable + zoomable, selected nodes highlighted */}
        <MiniMap
          position={minimap?.position ?? 'bottom-left'}
          pannable={minimap?.pannable ?? true}
          zoomable={minimap?.zoomable ?? true}
          nodeColor={minimap?.nodeColor ?? defaultNodeColor}
          nodeStrokeWidth={minimap?.nodeStrokeWidth ?? 3}
          // xyflow pipes maskColor through a CSS custom property, so a var()
          // resolves fine and the mask follows the theme (RF's default is a
          // light gray that glares on dark canvases). Chrome-gated: legacy
          // surfaces keep the RF default unless they pass their own.
          maskColor={
            minimap?.maskColor ??
            (themedChrome ? 'var(--canvas-mask, rgba(15, 20, 29, 0.6))' : undefined)
          }
          aria-label={minimap?.ariaLabel}
        />
        <GuideOverlay guides={guides} />
      </ReactFlow>
      {createMenu && renderCreateMenu?.(createMenu, () => setCreateMenu(null))}
    </div>
  );
}
