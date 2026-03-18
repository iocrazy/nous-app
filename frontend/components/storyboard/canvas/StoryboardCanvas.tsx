import React, { useState, useCallback, useMemo } from 'react';
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  ReactFlowProvider,
  useReactFlow,
  NodeChange,
  EdgeChange,
  Connection,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useStoryboardStore } from '../../../stores/storyboardStore';
import { useStoryboardCanvas } from '../../../hooks/storyboard/useStoryboardCanvas';

// Node components
import UploadNode from '../nodes/UploadNode';
import ImageEditNode from '../nodes/ImageEditNode';
import StoryboardSplitNode from '../nodes/StoryboardSplitNode';
import StoryboardGenNode from '../nodes/StoryboardGenNode';
import TextAnnotationNode from '../nodes/TextAnnotationNode';
import GroupNode from '../nodes/GroupNode';
import ExportNode from '../nodes/ExportNode';
import ImageToVideoNode from '../nodes/ImageToVideoNode';
import ImageNode from '../nodes/ImageNode';

// Canvas components
import CanvasToolbar from './CanvasToolbar';
import CanvasMiniMap from './CanvasMiniMap';
import NodeSelectionMenu from './NodeSelectionMenu';
import CanvasContextMenu, { ContextMenuState } from './CanvasContextMenu';
import SmartEdge from './edges/SmartEdge';

// ─── Node / Edge type registries ──────────────────────────────────────────────

const NODE_TYPES = {
  upload: UploadNode,
  image: ImageNode,
  image_edit: ImageEditNode,
  storyboard_split: StoryboardSplitNode,
  storyboard_gen: StoryboardGenNode,
  text_annotation: TextAnnotationNode,
  group: GroupNode,
  export: ExportNode,
  image_to_video: ImageToVideoNode,
} as const;

const EDGE_TYPES = {
  smart: SmartEdge,
} as const;

// ─── Initial context menu state ───────────────────────────────────────────────

const INITIAL_CONTEXT_MENU: ContextMenuState = {
  visible: false,
  x: 0,
  y: 0,
  targetNodeId: null,
};

// ─── Inner canvas (needs ReactFlow context) ───────────────────────────────────

function StoryboardCanvasInner() {
  const { nodes, edges, viewport, setViewport } = useStoryboardStore();
  const {
    onNodesChange,
    onEdgesChange,
    onConnect,
    pasteNodes,
    canPaste,
  } = useStoryboardCanvas();
  const { screenToFlowPosition, fitView } = useReactFlow();

  const [locked, setLocked] = useState(false);
  const [showCharacters, setShowCharacters] = useState(false);
  const [selectionMenu, setSelectionMenu] = useState<{
    visible: boolean;
    position: { x: number; y: number };
    connectingNodeId: string | null;
  }>({ visible: false, position: { x: 0, y: 0 }, connectingNodeId: null });

  // ─── Context menu state ───────────────────────────────────────────────
  const [contextMenu, setContextMenu] = useState<ContextMenuState>(INITIAL_CONTEXT_MENU);
  const [contextFlowPos, setContextFlowPos] = useState<{ x: number; y: number }>({ x: 0, y: 0 });

  // Convert store nodes to ReactFlow node format
  const rfNodes = useMemo(
    () =>
      nodes.map((n) => ({
        id: n.id,
        type: n.node_type,
        position: { x: n.position_x, y: n.position_y },
        data: { ...n.data_json, nodeId: n.id, locked: n.locked || locked },
        width: n.width,
        height: n.height,
        selected: false,
      })),
    [nodes, locked]
  );

  // Convert store edges to ReactFlow edge format
  const rfEdges = useMemo(
    () =>
      edges.map((e) => ({
        id: e.id,
        source: e.source_node_id,
        target: e.target_node_id,
        sourceHandle: e.source_handle,
        targetHandle: e.target_handle,
        type: 'smart',
      })),
    [edges]
  );

  const handleNodesChange = useCallback(
    (changes: NodeChange[]) => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      onNodesChange(changes as any);
    },
    [onNodesChange]
  );

  const handleEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      onEdgesChange(changes as any);
    },
    [onEdgesChange]
  );

  const handleConnect = useCallback(
    (connection: Connection) => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      onConnect(connection as any);
    },
    [onConnect]
  );

  const handleConnectEnd = useCallback(
    (event: MouseEvent | TouchEvent, connectionState: { fromNode?: { id: string } | null; isValid?: boolean }) => {
      if (connectionState?.isValid) return;

      const clientX = 'touches' in event ? event.touches[0]?.clientX : event.clientX;
      const clientY = 'touches' in event ? event.touches[0]?.clientY : event.clientY;

      if (clientX === undefined || clientY === undefined) return;

      const flowPosition = screenToFlowPosition({ x: clientX, y: clientY });

      setSelectionMenu({
        visible: true,
        position: { x: clientX, y: clientY },
        connectingNodeId: connectionState?.fromNode?.id ?? null,
      });

      // Store the flow position for node creation
      setSelectionMenu((prev) => ({
        ...prev,
        position: { x: flowPosition.x, y: flowPosition.y },
      }));
    },
    [screenToFlowPosition]
  );

  const handleCloseSelectionMenu = useCallback(() => {
    setSelectionMenu((prev) => ({ ...prev, visible: false, connectingNodeId: null }));
  }, []);

  const handleMoveEnd = useCallback(
    (_: unknown, newViewport: { x: number; y: number; zoom: number }) => {
      setViewport(newViewport);
    },
    [setViewport]
  );

  // ─── Context menu handler ─────────────────────────────────────────────

  const handleContextMenu = useCallback(
    (event: React.MouseEvent) => {
      event.preventDefault();

      const flowPos = screenToFlowPosition({ x: event.clientX, y: event.clientY });
      setContextFlowPos(flowPos);

      // Determine if the right-click is on a node
      const target = event.target as HTMLElement;
      const nodeElement = target.closest('.react-flow__node');
      const targetNodeId = nodeElement
        ? nodeElement.getAttribute('data-id')
        : null;

      setContextMenu({
        visible: true,
        x: event.clientX,
        y: event.clientY,
        targetNodeId,
      });
    },
    [screenToFlowPosition]
  );

  const handleCloseContextMenu = useCallback(() => {
    setContextMenu(INITIAL_CONTEXT_MENU);
  }, []);

  const handleFitView = useCallback(() => {
    fitView({ padding: 0.1 });
  }, [fitView]);

  const isEmpty = nodes.length === 0;

  return (
    <div className="relative w-full h-full bg-gray-950">
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={NODE_TYPES}
        edgeTypes={EDGE_TYPES}
        onNodesChange={handleNodesChange}
        onEdgesChange={handleEdgesChange}
        onConnect={handleConnect}
        onConnectEnd={handleConnectEnd}
        onMoveEnd={handleMoveEnd}
        onContextMenu={handleContextMenu}
        defaultViewport={viewport}
        nodesDraggable={!locked}
        nodesConnectable={!locked}
        elementsSelectable={!locked}
        fitView={isEmpty}
        deleteKeyCode="Delete"
        multiSelectionKeyCode="Shift"
        className="bg-gray-950"
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={20}
          size={1}
          color="#374151"
        />
        <Controls
          className="!bottom-4 !left-4 !bg-gray-800 !border-gray-700 !rounded-lg"
          showInteractive={false}
        />
        <CanvasMiniMap />
      </ReactFlow>

      <CanvasToolbar
        locked={locked}
        onLockToggle={() => setLocked((v) => !v)}
        onCharactersToggle={() => setShowCharacters((v) => !v)}
      />

      <NodeSelectionMenu
        visible={selectionMenu.visible}
        position={selectionMenu.position}
        connectingNodeId={selectionMenu.connectingNodeId}
        onClose={handleCloseSelectionMenu}
      />

      <CanvasContextMenu
        menu={contextMenu}
        onClose={handleCloseContextMenu}
        flowPosition={contextFlowPos}
        onFitView={handleFitView}
        onPaste={pasteNodes}
        canPaste={canPaste}
      />

      {isEmpty && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <div className="text-center">
            <p className="text-gray-500 text-lg font-medium">No nodes yet</p>
            <p className="text-gray-600 text-sm mt-1">
              Click <span className="text-blue-400 font-medium">Add</span> in the toolbar to get started
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── StoryboardCanvas (with provider) ────────────────────────────────────────

const StoryboardCanvas = React.memo(function StoryboardCanvas() {
  return (
    <ReactFlowProvider>
      <StoryboardCanvasInner />
    </ReactFlowProvider>
  );
});

export default StoryboardCanvas;
