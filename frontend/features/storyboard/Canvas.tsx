import {
  useState,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  type MouseEvent as ReactMouseEvent,
} from 'react';
import {
  ReactFlow,
  Background,
  MiniMap,
  BackgroundVariant,
  SelectionMode,
  useReactFlow,
  type Connection,
  type EdgeChange,
  type FinalConnectionState,
  type HandleType,
  type NodeChange,
  type OnConnectStartParams,
  type Viewport,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import { useCanvasStore } from '../../stores/canvasStore';
import { canvasAiGateway, canvasEventBus } from './application/canvasServices';
import {
  CANVAS_NODE_TYPES,
  type CanvasEdge,
  type CanvasNode,
  type CanvasNodeType,
  DEFAULT_NODE_WIDTH,
} from './domain/canvasNodes';
import { prepareNodeImage } from './application/imageData';
import {
  getConnectMenuNodeTypes,
  nodeHasSourceHandle,
  nodeHasTargetHandle,
} from './domain/nodeRegistry';
import { nodeTypes } from './nodes';
import { edgeTypes } from './edges';
import { NodeSelectionMenu } from './NodeSelectionMenu';
import { CanvasToolbar } from './CanvasToolbar';
import { SelectedNodeOverlay } from './ui/SelectedNodeOverlay';
import { NodeToolDialog } from './ui/NodeToolDialog';
import { ImageViewerModal } from './ui/ImageViewerModal';
import { useCanvasPersist } from './hooks/useCanvasPersist';

const DEFAULT_VIEWPORT: Viewport = { x: 0, y: 0, zoom: 1 };
const ALT_DRAG_COPY_Z_INDEX = 2000;
const GENERATION_JOB_POLL_INTERVAL_MS = 1400;
const SNAP_GRID_SIZE = 16;

const KEYBOARD_SHORTCUTS = [
  { keys: 'Ctrl+Z', label: 'Undo' },
  { keys: 'Ctrl+Shift+Z', label: 'Redo' },
  { keys: 'Ctrl+C', label: 'Copy' },
  { keys: 'Ctrl+V', label: 'Paste' },
  { keys: 'Ctrl+G', label: 'Group' },
  { keys: 'Del / Backspace', label: 'Delete' },
  { keys: 'Alt+Drag', label: 'Duplicate nodes' },
  { keys: 'Double-click', label: 'Add node' },
  { keys: 'Scroll', label: 'Zoom' },
  { keys: '?', label: 'Toggle shortcuts' },
] as const;

interface PendingConnectStart {
  nodeId: string;
  handleType: HandleType;
  start?: { x: number; y: number };
}

interface ClipboardSnapshot {
  nodes: CanvasNode[];
  edges: CanvasEdge[];
}

interface DuplicateOptions {
  explicitOffset?: { x: number; y: number };
  disableOffsetIteration?: boolean;
  suppressSelect?: boolean;
  suppressPersist?: boolean;
}

interface DuplicateResult {
  firstNodeId: string | null;
  idMap: Map<string, string>;
}

function getNodeSize(node: CanvasNode): { width: number; height: number } {
  const styleWidth = typeof node.style?.width === 'number' ? node.style.width : null;
  const styleHeight = typeof node.style?.height === 'number' ? node.style.height : null;
  return {
    width: node.measured?.width ?? styleWidth ?? DEFAULT_NODE_WIDTH,
    height: node.measured?.height ?? styleHeight ?? 200,
  };
}

function cloneNodeData<T>(value: T): T {
  if (typeof structuredClone === 'function') {
    return structuredClone(value);
  }
  return JSON.parse(JSON.stringify(value)) as T;
}

function isTypingTarget(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null;
  if (!element) return false;
  const tagName = element.tagName.toLowerCase();
  return tagName === 'input' || tagName === 'textarea' || element.isContentEditable;
}

function resolveClipboardImageFile(event: ClipboardEvent): File | null {
  const clipboardItems = event.clipboardData?.items;
  if (!clipboardItems) return null;
  for (const item of Array.from(clipboardItems)) {
    if (!item.type.startsWith('image/')) continue;
    const file = item.getAsFile();
    if (!file) continue;
    const existingName = typeof file.name === 'string' ? file.name.trim() : '';
    if (existingName) return file;
    const subtype = item.type.split('/')[1]?.split('+')[0] || 'png';
    return new File([file], `pasted-image.${subtype}`, { type: file.type || item.type, lastModified: Date.now() });
  }
  return null;
}

function hasRectCollision(
  candidateRect: { x: number; y: number; width: number; height: number },
  nodes: CanvasNode[],
  ignoreNodeIds: Set<string>
): boolean {
  const margin = 18;
  return nodes.some((node) => {
    if (ignoreNodeIds.has(node.id)) return false;
    const size = getNodeSize(node);
    return (
      candidateRect.x < node.position.x + size.width + margin &&
      candidateRect.x + candidateRect.width + margin > node.position.x &&
      candidateRect.y < node.position.y + size.height + margin &&
      candidateRect.y + candidateRect.height + margin > node.position.y
    );
  });
}

function canNodeTypeBeManualConnectionSource(type: CanvasNodeType): boolean {
  return type === CANVAS_NODE_TYPES.upload || type === CANVAS_NODE_TYPES.exportImage;
}

function canNodeBeManualConnectionSource(nodeId: string | null | undefined, nodes: CanvasNode[]): boolean {
  if (!nodeId) return false;
  const node = nodes.find((item) => item.id === nodeId);
  return node ? canNodeTypeBeManualConnectionSource(node.type) : false;
}

function getClientPosition(event: MouseEvent | TouchEvent): { x: number; y: number } | null {
  if ('clientX' in event && 'clientY' in event) return { x: event.clientX, y: event.clientY };
  const touch = 'changedTouches' in event ? event.changedTouches[0] ?? event.touches[0] : null;
  if (!touch) return null;
  return { x: touch.clientX, y: touch.clientY };
}

export function Canvas() {
  useCanvasPersist();

  const reactFlowInstance = useReactFlow();
  const wrapperRef = useRef<HTMLDivElement>(null);
  const suppressNextPaneClickRef = useRef(false);
  const suppressNextEdgeClickRef = useRef(false);

  const [isLocked, setIsLocked] = useState(false);
  const [snapToGrid, setSnapToGrid] = useState(false);
  const [showShortcutsHelp, setShowShortcutsHelp] = useState(false);
  const [showNodeMenu, setShowNodeMenu] = useState(false);
  const [menuPosition, setMenuPosition] = useState({ x: 0, y: 0 });
  const [flowPosition, setFlowPosition] = useState({ x: 0, y: 0 });
  const [menuAllowedTypes, setMenuAllowedTypes] = useState<CanvasNodeType[] | undefined>(undefined);
  const [pendingConnectStart, setPendingConnectStart] = useState<PendingConnectStart | null>(null);

  const copiedSnapshotRef = useRef<ClipboardSnapshot | null>(null);
  const pasteIterationRef = useRef(0);
  const pasteImageHandledRef = useRef(false);
  const duplicateNodesRef = useRef<((sourceNodeIds: string[]) => string | null) | null>(null);
  const activeGenerationPollNodeIdsRef = useRef(new Set<string>());
  const altDragCopyRef = useRef<{
    sourceNodeIds: string[];
    startPositions: Map<string, { x: number; y: number }>;
    copiedNodeIds: string[];
    sourceToCopyIdMap: Map<string, string>;
  } | null>(null);
  const edgePanGestureRef = useRef<{
    active: boolean;
    pointerId: number;
    startClientX: number;
    startClientY: number;
    startViewportX: number;
    startViewportY: number;
    zoom: number;
    moved: boolean;
  } | null>(null);

  const nodes = useCanvasStore((state) => state.nodes);
  const edges = useCanvasStore((state) => state.edges);
  const applyNodesChange = useCanvasStore((state) => state.onNodesChange);
  const applyEdgesChange = useCanvasStore((state) => state.onEdgesChange);
  const connectNodes = useCanvasStore((state) => state.onConnect);
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);
  const addNode = useCanvasStore((state) => state.addNode);
  const setSelectedNode = useCanvasStore((state) => state.setSelectedNode);
  const selectedNodeId = useCanvasStore((state) => state.selectedNodeId);
  const deleteEdge = useCanvasStore((state) => state.deleteEdge);
  const deleteNode = useCanvasStore((state) => state.deleteNode);
  const deleteNodes = useCanvasStore((state) => state.deleteNodes);
  const groupNodes = useCanvasStore((state) => state.groupNodes);
  const undo = useCanvasStore((state) => state.undo);
  const redo = useCanvasStore((state) => state.redo);
  const openToolDialog = useCanvasStore((state) => state.openToolDialog);
  const closeToolDialog = useCanvasStore((state) => state.closeToolDialog);
  const setViewportState = useCanvasStore((state) => state.setViewportState);
  const setCanvasViewportSize = useCanvasStore((state) => state.setCanvasViewportSize);
  const imageViewer = useCanvasStore((state) => state.imageViewer);
  const closeImageViewer = useCanvasStore((state) => state.closeImageViewer);
  const navigateImageViewer = useCanvasStore((state) => state.navigateImageViewer);

  useEffect(() => {
    const unsubOpen = canvasEventBus.subscribe('tool-dialog/open', (payload) => openToolDialog(payload));
    const unsubClose = canvasEventBus.subscribe('tool-dialog/close', () => closeToolDialog());
    return () => { unsubOpen(); unsubClose(); };
  }, [openToolDialog, closeToolDialog]);

  useEffect(() => {
    const element = wrapperRef.current;
    if (!element) return;
    const updateSize = () => {
      const rect = element.getBoundingClientRect();
      setCanvasViewportSize({ width: Math.max(0, Math.round(rect.width)), height: Math.max(0, Math.round(rect.height)) });
    };
    updateSize();
    const observer = new ResizeObserver(updateSize);
    observer.observe(element);
    return () => observer.disconnect();
  }, [setCanvasViewportSize]);

  const handleNodesChange = useCallback(
    (changes: NodeChange<CanvasNode>[]) => { applyNodesChange(changes); },
    [applyNodesChange]
  );

  const handleEdgesChange = useCallback(
    (changes: EdgeChange<CanvasEdge>[]) => { applyEdgesChange(changes); },
    [applyEdgesChange]
  );

  const handleEdgeDoubleClick = useCallback(
    (event: ReactMouseEvent, edge: CanvasEdge) => {
      event.preventDefault();
      event.stopPropagation();
      deleteEdge(edge.id);
    },
    [deleteEdge]
  );

  const handleEdgeClick = useCallback((event: ReactMouseEvent) => {
    if (!suppressNextEdgeClickRef.current) return;
    suppressNextEdgeClickRef.current = false;
    event.preventDefault();
    event.stopPropagation();
  }, []);

  // ─── Edge Pan Gesture ────────────────────────────────────────────────────
  useEffect(() => {
    const wrapperElement = wrapperRef.current;
    if (!wrapperElement) return;

    const edgePathSelector = '.react-flow__edge-path, .react-flow__edge-interaction';
    const dragThreshold = 4;

    const handlePointerDown = (event: PointerEvent) => {
      if (event.button !== 0) return;
      const target = event.target as HTMLElement | null;
      if (!target) return;
      if (target.closest('.react-flow__edgeupdater')) return;
      if (!target.closest(edgePathSelector)) return;

      const viewport = reactFlowInstance.getViewport();
      edgePanGestureRef.current = {
        active: true,
        pointerId: event.pointerId,
        startClientX: event.clientX,
        startClientY: event.clientY,
        startViewportX: viewport.x,
        startViewportY: viewport.y,
        zoom: viewport.zoom,
        moved: false,
      };
    };

    const handlePointerMove = (event: PointerEvent) => {
      const gesture = edgePanGestureRef.current;
      if (!gesture || !gesture.active || event.pointerId !== gesture.pointerId) return;

      const deltaX = event.clientX - gesture.startClientX;
      const deltaY = event.clientY - gesture.startClientY;
      if (!gesture.moved && Math.hypot(deltaX, deltaY) >= dragThreshold) {
        gesture.moved = true;
      }
      if (!gesture.moved) return;

      suppressNextEdgeClickRef.current = true;
      reactFlowInstance.setViewport(
        { x: gesture.startViewportX + deltaX, y: gesture.startViewportY + deltaY, zoom: gesture.zoom },
        { duration: 0 }
      );
    };

    const completeEdgePanGesture = () => {
      const gesture = edgePanGestureRef.current;
      if (!gesture) return;
      edgePanGestureRef.current = null;
      if (gesture.moved) {
        setViewportState(reactFlowInstance.getViewport());
      }
    };

    const handlePointerUp = (event: PointerEvent) => {
      if (edgePanGestureRef.current?.pointerId === event.pointerId) completeEdgePanGesture();
    };
    const handlePointerCancel = (event: PointerEvent) => {
      if (edgePanGestureRef.current?.pointerId === event.pointerId) completeEdgePanGesture();
    };

    wrapperElement.addEventListener('pointerdown', handlePointerDown, true);
    window.addEventListener('pointermove', handlePointerMove, true);
    window.addEventListener('pointerup', handlePointerUp, true);
    window.addEventListener('pointercancel', handlePointerCancel, true);

    return () => {
      wrapperElement.removeEventListener('pointerdown', handlePointerDown, true);
      window.removeEventListener('pointermove', handlePointerMove, true);
      window.removeEventListener('pointerup', handlePointerUp, true);
      window.removeEventListener('pointercancel', handlePointerCancel, true);
    };
  }, [reactFlowInstance, setViewportState]);

  const handleConnect = useCallback(
    (connection: Connection) => {
      if (!canNodeBeManualConnectionSource(connection.source, nodes)) return;
      connectNodes(connection);
    },
    [connectNodes, nodes]
  );

  const handleMoveEnd = useCallback(
    (_event: unknown, viewport: Viewport) => { setViewportState(viewport); },
    [setViewportState]
  );

  const handleMove = useCallback(
    (_event: unknown, viewport: Viewport) => { setViewportState(viewport); },
    [setViewportState]
  );

  const selectedNodeIds = useMemo(
    () => nodes.filter((node) => Boolean(node.selected)).map((node) => node.id),
    [nodes]
  );

  const selectedUploadNodeId = useMemo(() => {
    if (selectedNodeIds.length !== 1) return null;
    const selectedNode = nodes.find((node) => node.id === selectedNodeIds[0]);
    if (!selectedNode || selectedNode.type !== CANVAS_NODE_TYPES.upload) return null;
    return selectedNode.id;
  }, [nodes, selectedNodeIds]);

  useEffect(() => {
    if (selectedNodeIds.length === 1) {
      if (selectedNodeId !== selectedNodeIds[0]) setSelectedNode(selectedNodeIds[0]);
      return;
    }
    if (selectedNodeId !== null) setSelectedNode(null);
  }, [selectedNodeId, selectedNodeIds, setSelectedNode]);

  useEffect(() => {
    const handlePaste = (event: ClipboardEvent) => {
      pasteImageHandledRef.current = false;
      if (!selectedUploadNodeId || isTypingTarget(event.target)) return;
      const imageFile = resolveClipboardImageFile(event);
      if (!imageFile) return;
      event.preventDefault();
      pasteImageHandledRef.current = true;
      canvasEventBus.publish('upload-node/paste-image', { nodeId: selectedUploadNodeId, file: imageFile });
    };
    document.addEventListener('paste', handlePaste);
    return () => document.removeEventListener('paste', handlePaste);
  }, [selectedUploadNodeId]);

  // Duplicate nodes helper — supports Alt+Drag copy options
  const duplicateNodes = useCallback(
    (sourceNodeIds: string[], options: DuplicateOptions = {}): DuplicateResult | null => {
      const dedupedIds = Array.from(new Set(sourceNodeIds));
      if (dedupedIds.length === 0) return null;
      const sourceNodes = nodes.filter((node) => dedupedIds.includes(node.id));
      if (sourceNodes.length === 0) return null;
      const sourceIdSet = new Set(sourceNodes.map((node) => node.id));
      const internalEdges = edges.filter((edge) => sourceIdSet.has(edge.source) && sourceIdSet.has(edge.target));
      const offsetStep = options.disableOffsetIteration ? 0 : pasteIterationRef.current;
      const baseOffset = options.explicitOffset ?? { x: 44, y: 30 };
      const offset = {
        x: baseOffset.x + offsetStep * 8,
        y: baseOffset.y + offsetStep * 6,
      };
      const idMap = new Map<string, string>();
      for (const sourceNode of sourceNodes) {
        const data = cloneNodeData(sourceNode.data);
        // Clear generation state on cloned data
        const record = data as Record<string, unknown>;
        if ('isGenerating' in record) { (data as { isGenerating?: boolean }).isGenerating = false; }
        if ('generationJobId' in record) { (data as { generationJobId?: string | null }).generationJobId = null; }
        if ('generationStartedAt' in record) { (data as { generationStartedAt?: number | null }).generationStartedAt = null; }
        if ('generationError' in record) { (data as { generationError?: string | null }).generationError = null; }
        const nextNodeId = addNode(
          sourceNode.type as CanvasNodeType,
          { x: sourceNode.position.x + offset.x, y: sourceNode.position.y + offset.y },
          { ...data }
        );
        idMap.set(sourceNode.id, nextNodeId);
      }
      // Sync dimensions from source to copies
      const sizeSyncChanges = sourceNodes
        .map((sourceNode) => {
          const copyId = idMap.get(sourceNode.id);
          if (!copyId) return null;
          const size = getNodeSize(sourceNode);
          return { id: copyId, type: 'dimensions' as const, dimensions: size, resizing: false, setAttributes: true };
        })
        .filter(Boolean) as NodeChange<CanvasNode>[];
      if (sizeSyncChanges.length > 0) {
        applyNodesChange(sizeSyncChanges);
      }
      for (const edge of internalEdges) {
        const nextSource = idMap.get(edge.source);
        const nextTarget = idMap.get(edge.target);
        if (nextSource && nextTarget) {
          connectNodes({ source: nextSource, target: nextTarget, sourceHandle: edge.sourceHandle ?? 'source', targetHandle: edge.targetHandle ?? 'target' });
        }
      }
      if (!options.disableOffsetIteration) {
        pasteIterationRef.current += 1;
      }
      const firstNodeId = idMap.get(sourceNodes[0].id) ?? null;
      if (firstNodeId && !options.suppressSelect) {
        setSelectedNode(firstNodeId);
      }
      return { firstNodeId, idMap };
    },
    [addNode, applyNodesChange, connectNodes, edges, nodes, setSelectedNode]
  );

  useEffect(() => {
    duplicateNodesRef.current = (sourceNodeIds: string[]) => duplicateNodes(sourceNodeIds)?.firstNodeId ?? null;
  }, [duplicateNodes]);

  // ─── Alt+Drag to Duplicate ─────────────────────────────────────────────────
  const handleNodeDragStart = useCallback(
    (event: ReactMouseEvent, node: CanvasNode) => {
      if (!event.altKey) {
        altDragCopyRef.current = null;
        return;
      }
      const sourceNodeIds = selectedNodeIds.includes(node.id) ? selectedNodeIds : [node.id];
      if (sourceNodeIds.length === 0) { altDragCopyRef.current = null; return; }
      const startPositions = new Map<string, { x: number; y: number }>();
      for (const sourceNodeId of sourceNodeIds) {
        const sourceNode = nodes.find((item) => item.id === sourceNodeId);
        if (sourceNode) {
          startPositions.set(sourceNodeId, { x: sourceNode.position.x, y: sourceNode.position.y });
        }
      }
      if (startPositions.size === 0) { altDragCopyRef.current = null; return; }
      const result = duplicateNodes(sourceNodeIds, {
        explicitOffset: { x: 0, y: 0 },
        disableOffsetIteration: true,
        suppressPersist: true,
        suppressSelect: true,
      });
      if (!result) { altDragCopyRef.current = null; return; }
      const copiedNodeIds = sourceNodeIds
        .map((sid) => result.idMap.get(sid))
        .filter((cid): cid is string => Boolean(cid));
      if (copiedNodeIds.length === 0) { altDragCopyRef.current = null; return; }
      // Raise duplicated nodes above originals
      useCanvasStore.setState((state) => ({
        nodes: state.nodes.map((n) =>
          copiedNodeIds.includes(n.id)
            ? { ...n, zIndex: ALT_DRAG_COPY_Z_INDEX, style: { ...(n.style ?? {}), zIndex: ALT_DRAG_COPY_Z_INDEX } }
            : n
        ),
      }));
      altDragCopyRef.current = { sourceNodeIds, startPositions, copiedNodeIds, sourceToCopyIdMap: result.idMap };
    },
    [duplicateNodes, nodes, selectedNodeIds]
  );

  const handleNodeDrag = useCallback(
    (_event: ReactMouseEvent, node: CanvasNode) => {
      const altState = altDragCopyRef.current;
      if (!altState) return;
      const startPos = altState.startPositions.get(node.id);
      if (!startPos) return;
      const dx = node.position.x - startPos.x;
      const dy = node.position.y - startPos.y;
      // Restore originals to start positions and move copies to delta
      const restoreChanges = altState.sourceNodeIds
        .map((sid) => {
          const sp = altState.startPositions.get(sid);
          if (!sp) return null;
          return { id: sid, type: 'position' as const, position: sp, dragging: true };
        })
        .filter(Boolean) as NodeChange<CanvasNode>[];
      const moveChanges = altState.sourceNodeIds
        .map((sid) => {
          const sp = altState.startPositions.get(sid);
          const cid = altState.sourceToCopyIdMap.get(sid);
          if (!sp || !cid) return null;
          return { id: cid, type: 'position' as const, position: { x: sp.x + dx, y: sp.y + dy }, dragging: true };
        })
        .filter(Boolean) as NodeChange<CanvasNode>[];
      const allChanges = [...restoreChanges, ...moveChanges];
      if (allChanges.length > 0) applyNodesChange(allChanges);
    },
    [applyNodesChange]
  );

  const handleNodeDragStop = useCallback(
    (_event: ReactMouseEvent, node: CanvasNode) => {
      const altState = altDragCopyRef.current;
      if (!altState) return;
      altDragCopyRef.current = null;
      const startPos = altState.startPositions.get(node.id);
      if (!startPos) return;
      const dx = node.position.x - startPos.x;
      const dy = node.position.y - startPos.y;
      const restoreChanges = altState.sourceNodeIds
        .map((sid) => {
          const sp = altState.startPositions.get(sid);
          if (!sp) return null;
          return { id: sid, type: 'position' as const, position: sp, dragging: false };
        })
        .filter(Boolean) as NodeChange<CanvasNode>[];
      const finalizeChanges = altState.sourceNodeIds
        .map((sid) => {
          const sp = altState.startPositions.get(sid);
          const cid = altState.sourceToCopyIdMap.get(sid);
          if (!sp || !cid) return null;
          return { id: cid, type: 'position' as const, position: { x: sp.x + dx, y: sp.y + dy }, dragging: false };
        })
        .filter(Boolean) as NodeChange<CanvasNode>[];
      const allChanges = [...restoreChanges, ...finalizeChanges];
      if (allChanges.length > 0) applyNodesChange(allChanges);
      if (altState.copiedNodeIds.length > 0) setSelectedNode(altState.copiedNodeIds[0]);
    },
    [applyNodesChange, setSelectedNode]
  );

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (isTypingTarget(event.target)) return;
      const cmd = event.ctrlKey || event.metaKey;
      const key = event.key.toLowerCase();

      if (cmd && key === 'c' && !event.shiftKey) {
        if (selectedNodeIds.length === 0) return;
        event.preventDefault();
        const selectedIdSet = new Set(selectedNodeIds);
        copiedSnapshotRef.current = {
          nodes: nodes.filter((n) => selectedIdSet.has(n.id)),
          edges: edges.filter((e) => selectedIdSet.has(e.source) && selectedIdSet.has(e.target)),
        };
        return;
      }

      if (cmd && key === 'v' && !event.shiftKey) {
        if (!copiedSnapshotRef.current || copiedSnapshotRef.current.nodes.length === 0) return;
        event.preventDefault();
        duplicateNodesRef.current?.(copiedSnapshotRef.current.nodes.map((n) => n.id));
        return;
      }

      if (cmd && key === 'z' && !event.shiftKey) { event.preventDefault(); undo(); return; }
      if (cmd && (key === 'y' || (key === 'z' && event.shiftKey))) { event.preventDefault(); redo(); return; }
      if (cmd && key === 'g') {
        if (selectedNodeIds.length < 2) return;
        event.preventDefault();
        groupNodes(selectedNodeIds);
        return;
      }

      // Select all (Cmd+A)
      if (cmd && key === 'a') {
        event.preventDefault();
        const selectChanges: NodeChange<CanvasNode>[] = nodes.map((n) => ({
          id: n.id,
          type: 'select' as const,
          selected: true,
        }));
        applyNodesChange(selectChanges);
        return;
      }

      // Toggle keyboard shortcuts help (?)
      if (event.key === '?' || (event.shiftKey && key === '/')) {
        event.preventDefault();
        setShowShortcutsHelp((prev) => !prev);
        return;
      }

      // Deselect all (Escape)
      if (event.key === 'Escape') {
        if (showShortcutsHelp) { setShowShortcutsHelp(false); return; }
        setSelectedNode(null);
        setShowNodeMenu(false);
        return;
      }

      if (event.key === 'Delete' || event.key === 'Backspace') {
        const idsToDelete = selectedNodeIds.length > 0 ? selectedNodeIds : selectedNodeId ? [selectedNodeId] : [];
        if (idsToDelete.length === 0) return;
        event.preventDefault();
        if (idsToDelete.length === 1) deleteNode(idsToDelete[0]);
        else deleteNodes(idsToDelete);
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [applyNodesChange, edges, nodes, selectedNodeId, selectedNodeIds, deleteNode, deleteNodes, groupNodes, undo, redo, selectedUploadNodeId, setSelectedNode, showShortcutsHelp]);

  const openNodeMenuAtClientPosition = useCallback((clientX: number, clientY: number) => {
    const containerRect = wrapperRef.current?.getBoundingClientRect();
    if (!containerRect) return;
    const flowPos = reactFlowInstance.screenToFlowPosition({ x: clientX, y: clientY });
    setFlowPosition(flowPos);
    setMenuPosition({ x: clientX - containerRect.left, y: clientY - containerRect.top });
    setMenuAllowedTypes(undefined);
    setPendingConnectStart(null);
    setShowNodeMenu(true);
  }, [reactFlowInstance]);

  const handlePaneClick = useCallback((event: ReactMouseEvent) => {
    if (suppressNextPaneClickRef.current) { suppressNextPaneClickRef.current = false; return; }
    if (event.detail >= 2) { openNodeMenuAtClientPosition(event.clientX, event.clientY); return; }
    setSelectedNode(null);
    setShowNodeMenu(false);
    setMenuAllowedTypes(undefined);
    setPendingConnectStart(null);
  }, [openNodeMenuAtClientPosition, setSelectedNode]);

  const handleNodeSelect = useCallback(
    (type: CanvasNodeType) => {
      const newNodeId = addNode(type, flowPosition);
      if (pendingConnectStart) {
        if (pendingConnectStart.handleType === 'source') {
          connectNodes({ source: pendingConnectStart.nodeId, target: newNodeId, sourceHandle: 'source', targetHandle: 'target' });
        } else {
          connectNodes({ source: newNodeId, target: pendingConnectStart.nodeId, sourceHandle: 'source', targetHandle: 'target' });
        }
      }
      setShowNodeMenu(false);
      setMenuAllowedTypes(undefined);
      setPendingConnectStart(null);
    },
    [addNode, connectNodes, flowPosition, pendingConnectStart]
  );

  const handleConnectStart = useCallback(
    (_event: MouseEvent | TouchEvent, params: OnConnectStartParams) => {
      setShowNodeMenu(false);
      setMenuAllowedTypes(undefined);
      if (!params.nodeId || !params.handleType) { setPendingConnectStart(null); return; }
      if (params.handleType === 'source' && !canNodeBeManualConnectionSource(params.nodeId, nodes)) { setPendingConnectStart(null); return; }
      setPendingConnectStart({ nodeId: params.nodeId, handleType: params.handleType });
    },
    [nodes]
  );

  const handleConnectEnd = useCallback(
    (event: MouseEvent | TouchEvent, connectionState: FinalConnectionState) => {
      if (connectionState.isValid || !pendingConnectStart) { setPendingConnectStart(null); return; }
      const clientPosition = getClientPosition(event);
      const containerRect = wrapperRef.current?.getBoundingClientRect();
      if (!clientPosition || !containerRect) { setPendingConnectStart(null); return; }

      // Check if dropped on existing node for connection
      const dropNodeElement = (event.target as Element)?.closest?.('.react-flow__node[data-id]') as HTMLElement | null;
      const dropNodeId = dropNodeElement?.dataset?.id ?? null;
      if (dropNodeId && dropNodeId !== pendingConnectStart.nodeId) {
        const sourceNode = pendingConnectStart.handleType === 'source' ? nodes.find((n) => n.id === pendingConnectStart.nodeId) : nodes.find((n) => n.id === dropNodeId);
        const targetNode = pendingConnectStart.handleType === 'source' ? nodes.find((n) => n.id === dropNodeId) : nodes.find((n) => n.id === pendingConnectStart.nodeId);
        if (sourceNode && targetNode && canNodeTypeBeManualConnectionSource(sourceNode.type) && nodeHasSourceHandle(sourceNode.type) && nodeHasTargetHandle(targetNode.type)) {
          connectNodes({ source: sourceNode.id, target: targetNode.id, sourceHandle: 'source', targetHandle: 'target' });
          setPendingConnectStart(null);
          return;
        }
      }

      const allowedTypes = getConnectMenuNodeTypes(pendingConnectStart.handleType);
      if (allowedTypes.length === 0) { setPendingConnectStart(null); return; }

      const flowPos = reactFlowInstance.screenToFlowPosition(clientPosition);
      setFlowPosition(flowPos);
      setMenuPosition({ x: clientPosition.x - containerRect.left, y: clientPosition.y - containerRect.top });
      setMenuAllowedTypes(allowedTypes);
      suppressNextPaneClickRef.current = true;
      setShowNodeMenu(true);
    },
    [connectNodes, nodes, pendingConnectStart, reactFlowInstance]
  );

  // ─── AI Generation Job Polling ────────────────────────────────────────────
  useEffect(() => {
    const POLL_INTERVAL_MS = 1400;
    const sleep = (ms: number) => new Promise<void>((resolve) => { window.setTimeout(resolve, ms); });

    const pendingExportNodes = nodes.filter((node) => {
      if (node.type !== CANVAS_NODE_TYPES.exportImage) return false;
      const d = node.data as Record<string, unknown>;
      return d.isGenerating === true && typeof d.generationJobId === 'string' && (d.generationJobId as string).length > 0;
    });

    for (const pendingNode of pendingExportNodes) {
      if (activeGenerationPollNodeIdsRef.current.has(pendingNode.id)) continue;
      activeGenerationPollNodeIdsRef.current.add(pendingNode.id);

      void (async () => {
        try {
          while (true) {
            const currentNode = useCanvasStore.getState().nodes.find((n) => n.id === pendingNode.id);
            if (!currentNode) break;
            const currentData = currentNode.data as Record<string, unknown>;
            const jobId = typeof currentData.generationJobId === 'string' ? currentData.generationJobId : '';
            if (!jobId || currentData.isGenerating !== true) break;

            const status = await canvasAiGateway.getGenerateImageJob(jobId).catch((err) => {
              console.warn('[GenerationJob] poll failed', { nodeId: pendingNode.id, jobId, error: err });
              return null;
            });
            if (!status) { await sleep(POLL_INTERVAL_MS); continue; }

            if (status.status === 'queued' || status.status === 'running') {
              await sleep(POLL_INTERVAL_MS);
              continue;
            }

            if (status.status === 'succeeded' && typeof status.result === 'string' && status.result.trim()) {
              const prepared = await prepareNodeImage(status.result);
              updateNodeData(pendingNode.id, {
                imageUrl: prepared.imageUrl,
                previewImageUrl: prepared.previewImageUrl,
                aspectRatio: prepared.aspectRatio,
                isGenerating: false,
                generationStartedAt: null,
                generationJobId: null,
                generationProviderId: null,
                generationError: null,
              });
              break;
            }

            const errorMessage = status.error ?? (status.status === 'not_found' ? 'Job not found' : 'Generation failed');
            updateNodeData(pendingNode.id, {
              isGenerating: false,
              generationStartedAt: null,
              generationJobId: null,
              generationProviderId: null,
              generationError: errorMessage,
            });
            break;
          }
        } finally {
          activeGenerationPollNodeIdsRef.current.delete(pendingNode.id);
        }
      })();
    }
  }, [nodes, updateNodeData]);

  return (
    <div ref={wrapperRef} className="relative h-full w-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={handleNodesChange}
        onEdgesChange={handleEdgesChange}
        onEdgeDoubleClick={handleEdgeDoubleClick}
        onEdgeClick={handleEdgeClick}
        onConnect={handleConnect}
        onConnectStart={handleConnectStart}
        onConnectEnd={handleConnectEnd}
        onNodeDragStart={handleNodeDragStart}
        onNodeDrag={handleNodeDrag}
        onNodeDragStop={handleNodeDragStop}
        onPaneClick={handlePaneClick}
        onMove={handleMove}
        onMoveEnd={handleMoveEnd}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        defaultEdgeOptions={{ type: 'disconnectableEdge' }}
        defaultViewport={DEFAULT_VIEWPORT}
        minZoom={0.1}
        maxZoom={5}
        autoPanOnNodeDrag
        selectionOnDrag
        selectionMode={SelectionMode.Partial}
        multiSelectionKeyCode={['Control', 'Meta']}
        selectionKeyCode={['Control', 'Meta']}
        deleteKeyCode={null}
        onlyRenderVisibleElements
        zoomOnDoubleClick={false}
        snapToGrid={snapToGrid}
        snapGrid={[SNAP_GRID_SIZE, SNAP_GRID_SIZE]}
        proOptions={{ hideAttribution: true }}
        className="bg-bg-dark [&_.react-flow__viewport]:transition-transform"
      >
        <Background variant={BackgroundVariant.Dots} gap={24} size={1.2} color="#3f3f46" />
        <MiniMap
          className="canvas-minimap nopan nowheel !border-border-dark !bg-surface-dark"
          style={{ pointerEvents: 'all', zIndex: 10000 }}
          nodeColor="rgba(120, 120, 120, 0.92)"
          maskColor="rgba(0, 0, 0, 0.62)"
          pannable
          zoomable
        />
        <SelectedNodeOverlay />
      </ReactFlow>

      {/* Vignette overlay for infinite canvas feel */}
      <div className="canvas-vignette" />

      <CanvasToolbar isLocked={isLocked} onToggleLock={() => setIsLocked(v => !v)}
        snapToGrid={snapToGrid} onToggleSnap={() => setSnapToGrid((v) => !v)} />

      {nodes.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <div className="flex max-w-3xl flex-col items-center gap-5 px-6 text-center">
            <div>
              <div className="mb-2 text-2xl text-text-muted">Double-click to add a node</div>
              <div className="text-sm text-text-muted opacity-60">Upload images, edit, and create storyboards</div>
            </div>
          </div>
        </div>
      )}

      {showNodeMenu && (
        <NodeSelectionMenu
          position={menuPosition}
          allowedTypes={menuAllowedTypes}
          onSelect={handleNodeSelect}
          onClose={() => {
            setShowNodeMenu(false);
            setMenuAllowedTypes(undefined);
            setPendingConnectStart(null);
          }}
        />
      )}

      <NodeToolDialog />

      <ImageViewerModal
        open={imageViewer.isOpen}
        imageUrl={imageViewer.currentImageUrl || ''}
        imageList={imageViewer.imageList}
        currentIndex={imageViewer.currentIndex}
        onClose={closeImageViewer}
        onNavigate={navigateImageViewer}
      />

      {/* Zoom level indicator */}
      <ZoomIndicator />

      {/* Keyboard shortcuts help overlay */}
      {showShortcutsHelp && (
        <div className="absolute inset-0 z-[300] flex items-center justify-center bg-black/50"
          onClick={() => setShowShortcutsHelp(false)}>
          <div className="w-72 rounded-xl border border-[rgba(255,255,255,0.14)] bg-surface-dark p-4 shadow-2xl"
            onClick={(e) => e.stopPropagation()}>
            <h3 className="mb-3 text-sm font-medium text-text-dark">Keyboard Shortcuts</h3>
            <div className="flex flex-col gap-1.5">
              {KEYBOARD_SHORTCUTS.map((shortcut) => (
                <div key={shortcut.keys} className="flex items-center justify-between text-xs">
                  <span className="text-text-muted">{shortcut.label}</span>
                  <kbd className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] font-mono text-text-dark">{shortcut.keys}</kbd>
                </div>
              ))}
            </div>
            <div className="mt-3 text-center text-[10px] text-text-muted">Press ? or Esc to close</div>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Zoom Indicator ─────────────────────────────────────────────────────────

function ZoomIndicator() {
  const currentViewport = useCanvasStore((s) => s.currentViewport);
  const zoomLevel = currentViewport?.zoom ?? 1;
  const zoomPercent = Math.round(zoomLevel * 100);

  return (
    <div className="absolute bottom-3 left-3 z-20 flex items-center rounded-full border border-[rgba(255,255,255,0.12)] bg-surface-dark/80 px-2 py-1 text-[10px] text-text-muted backdrop-blur-sm">
      {zoomPercent}%
    </div>
  );
}
