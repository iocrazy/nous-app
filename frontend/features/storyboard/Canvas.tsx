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
import { canvasEventBus } from './application/canvasServices';
import {
  CANVAS_NODE_TYPES,
  type CanvasEdge,
  type CanvasNode,
  type CanvasNodeType,
  DEFAULT_NODE_WIDTH,
} from './domain/canvasNodes';
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

const DEFAULT_VIEWPORT: Viewport = { x: 0, y: 0, zoom: 1 };

interface PendingConnectStart {
  nodeId: string;
  handleType: HandleType;
  start?: { x: number; y: number };
}

interface ClipboardSnapshot {
  nodes: CanvasNode[];
  edges: CanvasEdge[];
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
  const reactFlowInstance = useReactFlow();
  const wrapperRef = useRef<HTMLDivElement>(null);
  const suppressNextPaneClickRef = useRef(false);

  const [isLocked, setIsLocked] = useState(false);
  const [showNodeMenu, setShowNodeMenu] = useState(false);
  const [menuPosition, setMenuPosition] = useState({ x: 0, y: 0 });
  const [flowPosition, setFlowPosition] = useState({ x: 0, y: 0 });
  const [menuAllowedTypes, setMenuAllowedTypes] = useState<CanvasNodeType[] | undefined>(undefined);
  const [pendingConnectStart, setPendingConnectStart] = useState<PendingConnectStart | null>(null);

  const copiedSnapshotRef = useRef<ClipboardSnapshot | null>(null);
  const pasteIterationRef = useRef(0);
  const pasteImageHandledRef = useRef(false);
  const duplicateNodesRef = useRef<((sourceNodeIds: string[]) => string | null) | null>(null);

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

  // Duplicate nodes helper
  const duplicateNodes = useCallback(
    (sourceNodeIds: string[]) => {
      const dedupedIds = Array.from(new Set(sourceNodeIds));
      if (dedupedIds.length === 0) return null;
      const sourceNodes = nodes.filter((node) => dedupedIds.includes(node.id));
      if (sourceNodes.length === 0) return null;
      const sourceIdSet = new Set(sourceNodes.map((node) => node.id));
      const internalEdges = edges.filter((edge) => sourceIdSet.has(edge.source) && sourceIdSet.has(edge.target));
      const offset = { x: 44 + pasteIterationRef.current * 8, y: 30 + pasteIterationRef.current * 6 };
      const idMap = new Map<string, string>();
      for (const sourceNode of sourceNodes) {
        const data = cloneNodeData(sourceNode.data);
        const nextNodeId = addNode(
          sourceNode.type as CanvasNodeType,
          { x: sourceNode.position.x + offset.x, y: sourceNode.position.y + offset.y },
          { ...data }
        );
        idMap.set(sourceNode.id, nextNodeId);
      }
      for (const edge of internalEdges) {
        const nextSource = idMap.get(edge.source);
        const nextTarget = idMap.get(edge.target);
        if (nextSource && nextTarget) {
          connectNodes({ source: nextSource, target: nextTarget, sourceHandle: edge.sourceHandle ?? 'source', targetHandle: edge.targetHandle ?? 'target' });
        }
      }
      pasteIterationRef.current += 1;
      const firstNodeId = idMap.get(sourceNodes[0].id) ?? null;
      if (firstNodeId) setSelectedNode(firstNodeId);
      return firstNodeId;
    },
    [addNode, connectNodes, edges, nodes, setSelectedNode]
  );

  useEffect(() => {
    duplicateNodesRef.current = (sourceNodeIds: string[]) => duplicateNodes(sourceNodeIds);
  }, [duplicateNodes]);

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
  }, [edges, nodes, selectedNodeId, selectedNodeIds, deleteNode, deleteNodes, groupNodes, undo, redo, selectedUploadNodeId]);

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

  return (
    <div ref={wrapperRef} className="relative h-full w-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={handleNodesChange}
        onEdgesChange={handleEdgesChange}
        onEdgeDoubleClick={handleEdgeDoubleClick}
        onConnect={handleConnect}
        onConnectStart={handleConnectStart}
        onConnectEnd={handleConnectEnd}
        onPaneClick={handlePaneClick}
        onMove={handleMove}
        onMoveEnd={handleMoveEnd}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        defaultEdgeOptions={{ type: 'disconnectableEdge' }}
        defaultViewport={DEFAULT_VIEWPORT}
        minZoom={0.1}
        maxZoom={5}
        selectionOnDrag
        selectionMode={SelectionMode.Partial}
        multiSelectionKeyCode={['Control', 'Meta']}
        selectionKeyCode={['Control', 'Meta']}
        deleteKeyCode={null}
        onlyRenderVisibleElements
        zoomOnDoubleClick={false}
        proOptions={{ hideAttribution: true }}
        className="bg-bg-dark"
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#2a2a2a" />
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

      <CanvasToolbar isLocked={isLocked} onToggleLock={() => setIsLocked(v => !v)} />

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
    </div>
  );
}
