import { create } from 'zustand';
import {
  Connection,
  EdgeChange,
  NodeChange,
  type Viewport,
  addEdge,
  applyEdgeChanges,
} from '@xyflow/react';

import {
  CANVAS_NODE_TYPES,
  type ActiveToolDialog,
  type CanvasEdge,
  type CanvasNode,
  type CanvasNodeData,
  type CanvasNodeType,
  type ExportImageNodeResultKind,
  type NodeToolType,
  type StoryboardFrameItem,
} from '../features/storyboard/domain/canvasNodes';
import {
  nodeHasSourceHandle,
  nodeHasTargetHandle,
} from '../features/storyboard/domain/nodeRegistry';
import { canvasNodeFactory } from '../features/storyboard/application/canvasServices';

import {
  normalizeHandleId,
  normalizeEdgesWithNodes,
  normalizeNodes,
  normalizeHistory,
  createSnapshot,
  pushSnapshot,
  resolveSelectedNodeId,
  resolveActiveToolDialog,
  computeNodePosition,
  type CanvasHistorySnapshot,
  type CanvasHistoryState,
} from './canvasStore/helpers';

import { applyUpdateStoryboardFrame, applyReorderStoryboardFrame } from './canvasStore/storyboardActions';
import { applyGroupNodes, applyUngroupNode } from './canvasStore/nodeGroupActions';
import { applyAddDerivedUploadNode, applyAddDerivedExportNode, applyAddStoryboardSplitNode } from './canvasStore/nodeAddActions';
import { applyNodesChange, applyUpdateNodeData, applyDeleteNodes, applyUndo, applyRedo } from './canvasStore/nodeChangeActions';

export type {
  ActiveToolDialog,
  CanvasEdge,
  CanvasNode,
  CanvasNodeData,
  CanvasNodeType,
  NodeToolType,
  StoryboardFrameItem,
};

export type { CanvasHistorySnapshot, CanvasHistoryState };

interface CanvasState {
  nodes: CanvasNode[];
  edges: CanvasEdge[];
  selectedNodeId: string | null;
  activeToolDialog: ActiveToolDialog | null;
  history: CanvasHistoryState;
  dragHistorySnapshot: CanvasHistorySnapshot | null;
  currentViewport: Viewport;
  canvasViewportSize: { width: number; height: number };
  imageViewer: {
    isOpen: boolean;
    currentImageUrl: string | null;
    imageList: string[];
    currentIndex: number;
  };

  onNodesChange: (changes: NodeChange<CanvasNode>[]) => void;
  onEdgesChange: (changes: EdgeChange<CanvasEdge>[]) => void;
  onConnect: (connection: Connection) => void;

  setCanvasData: (nodes: CanvasNode[], edges: CanvasEdge[], history?: CanvasHistoryState) => void;
  addNode: (
    type: CanvasNodeType,
    position: { x: number; y: number },
    data?: Partial<CanvasNodeData>
  ) => string;
  addEdge: (source: string, target: string) => string | null;
  findNodePosition: (sourceNodeId: string, newNodeWidth: number, newNodeHeight: number) => { x: number; y: number };
  addDerivedUploadNode: (
    sourceNodeId: string,
    imageUrl: string,
    aspectRatio: string,
    previewImageUrl?: string
  ) => string | null;
  addDerivedExportNode: (
    sourceNodeId: string,
    imageUrl: string,
    aspectRatio: string,
    previewImageUrl?: string,
    options?: {
      defaultTitle?: string;
      resultKind?: ExportImageNodeResultKind;
      aspectRatioStrategy?: 'provided' | 'derivedFromSource';
      sizeStrategy?: 'generated' | 'autoMinEdge' | 'matchSource';
      matchSourceNodeSize?: boolean;
    }
  ) => string | null;
  addStoryboardSplitNode: (
    sourceNodeId: string,
    rows: number,
    cols: number,
    frames: StoryboardFrameItem[],
    frameAspectRatio?: string
  ) => string | null;

  updateNodeData: (nodeId: string, data: Partial<CanvasNodeData>) => void;
  updateNodePosition: (nodeId: string, position: { x: number; y: number }) => void;
  updateStoryboardFrame: (
    nodeId: string,
    frameId: string,
    data: Partial<StoryboardFrameItem>
  ) => void;
  reorderStoryboardFrame: (
    nodeId: string,
    draggedFrameId: string,
    targetFrameId: string
  ) => void;

  deleteNode: (nodeId: string) => void;
  deleteNodes: (nodeIds: string[]) => void;
  groupNodes: (nodeIds: string[]) => string | null;
  ungroupNode: (groupNodeId: string) => boolean;
  deleteEdge: (edgeId: string) => void;
  setSelectedNode: (nodeId: string | null) => void;

  openToolDialog: (dialog: ActiveToolDialog) => void;
  closeToolDialog: () => void;
  setViewportState: (viewport: Viewport) => void;
  setCanvasViewportSize: (size: { width: number; height: number }) => void;
  openImageViewer: (imageUrl: string, imageList?: string[]) => void;
  closeImageViewer: () => void;
  navigateImageViewer: (direction: 'prev' | 'next') => void;

  undo: () => boolean;
  redo: () => boolean;

  clearCanvas: () => void;
}

export const useCanvasStore = create<CanvasState>((set, get) => ({
  nodes: [],
  edges: [],
  selectedNodeId: null,
  activeToolDialog: null,
  history: { past: [], future: [] },
  dragHistorySnapshot: null,
  currentViewport: { x: 0, y: 0, zoom: 1 },
  canvasViewportSize: { width: 0, height: 0 },
  imageViewer: {
    isOpen: false,
    currentImageUrl: null,
    imageList: [],
    currentIndex: 0,
  },

  onNodesChange: (changes) => {
    set((state) => applyNodesChange(state, changes));
  },

  onEdgesChange: (changes) => {
    set((state) => {
      const nextEdges = applyEdgeChanges<CanvasEdge>(changes, state.edges);
      const hasMeaningfulChange = changes.some((change) => change.type !== 'select');

      if (!hasMeaningfulChange) {
        return { edges: nextEdges };
      }

      return {
        edges: nextEdges,
        history: {
          past: pushSnapshot(state.history.past, createSnapshot(state.nodes, state.edges)),
          future: [],
        },
        dragHistorySnapshot: null,
      };
    });
  },

  onConnect: (connection) => {
    const sourceHandle = normalizeHandleId(connection.sourceHandle) ?? 'source';
    const targetHandle = normalizeHandleId(connection.targetHandle) ?? 'target';
    set((state) => ({
      edges: addEdge<CanvasEdge>(
        { ...connection, sourceHandle, targetHandle, type: 'disconnectableEdge' },
        state.edges
      ),
      history: {
        past: pushSnapshot(state.history.past, createSnapshot(state.nodes, state.edges)),
        future: [],
      },
      dragHistorySnapshot: null,
    }));
  },

  setCanvasData: (nodes, edges, history) => {
    const normalizedNodes = normalizeNodes(nodes);
    const normalizedEdges = normalizeEdgesWithNodes(edges, normalizedNodes);

    set({
      nodes: normalizedNodes,
      edges: normalizedEdges,
      selectedNodeId: null,
      activeToolDialog: null,
      history: normalizeHistory(history),
      dragHistorySnapshot: null,
    });
  },

  setViewportState: (viewport) => {
    set({ currentViewport: viewport });
  },

  setCanvasViewportSize: (size) => {
    set({ canvasViewportSize: size });
  },

  openImageViewer: (imageUrl, imageList = []) => {
    const list = imageList.length > 0 ? imageList : [imageUrl];
    const index = list.indexOf(imageUrl);
    set({
      imageViewer: {
        isOpen: true,
        currentImageUrl: imageUrl,
        imageList: list,
        currentIndex: index >= 0 ? index : 0,
      },
    });
  },

  closeImageViewer: () => {
    set({
      imageViewer: {
        isOpen: false,
        currentImageUrl: null,
        imageList: [],
        currentIndex: 0,
      },
    });
  },

  navigateImageViewer: (direction) => {
    const state = get();
    const { currentIndex, imageList } = state.imageViewer;
    if (direction === 'prev' && currentIndex > 0) {
      const newIndex = currentIndex - 1;
      set({
        imageViewer: {
          ...state.imageViewer,
          currentIndex: newIndex,
          currentImageUrl: imageList[newIndex],
        },
      });
    } else if (direction === 'next' && currentIndex < imageList.length - 1) {
      const newIndex = currentIndex + 1;
      set({
        imageViewer: {
          ...state.imageViewer,
          currentIndex: newIndex,
          currentImageUrl: imageList[newIndex],
        },
      });
    }
  },

  addNode: (type, position, data = {}) => {
    const state = get();
    const newNode = canvasNodeFactory.createNode(type, position, data);
    set({
      nodes: [...state.nodes, newNode],
      history: {
        past: pushSnapshot(state.history.past, createSnapshot(state.nodes, state.edges)),
        future: [],
      },
      dragHistorySnapshot: null,
    });
    // TODO: migrate — notify project store of dirty state
    return newNode.id;
  },

  addEdge: (source, target) => {
    const state = get();
    const sourceNode = state.nodes.find((n) => n.id === source);
    const targetNode = state.nodes.find((n) => n.id === target);
    if (!sourceNode || !targetNode) {
      return null;
    }
    if (!nodeHasSourceHandle(sourceNode.type) || !nodeHasTargetHandle(targetNode.type)) {
      return null;
    }

    const edgeId = `e-${source}-${target}`;
    if (state.edges.some((e) => e.id === edgeId)) {
      return edgeId;
    }

    const newEdge: CanvasEdge = {
      id: edgeId,
      source,
      target,
      sourceHandle: 'source',
      targetHandle: 'target',
      type: 'disconnectableEdge',
    };

    set({
      edges: [...state.edges, newEdge],
    });

    return edgeId;
  },

  findNodePosition: (sourceNodeId, newNodeWidth, newNodeHeight) => {
    const state = get();
    return computeNodePosition(
      {
        nodes: state.nodes,
        currentViewport: state.currentViewport,
        canvasViewportSize: state.canvasViewportSize,
      },
      sourceNodeId,
      newNodeWidth,
      newNodeHeight
    );
  },

  addDerivedUploadNode: (sourceNodeId, imageUrl, aspectRatio, previewImageUrl) => {
    const state = get();
    const { nodeId, patch } = applyAddDerivedUploadNode(state, sourceNodeId, imageUrl, aspectRatio, previewImageUrl);
    set(patch);
    return nodeId;
  },

  addDerivedExportNode: (sourceNodeId, imageUrl, aspectRatio, previewImageUrl, options) => {
    const state = get();
    const { nodeId, patch } = applyAddDerivedExportNode(state, sourceNodeId, imageUrl, aspectRatio, previewImageUrl, options);
    set(patch);
    return nodeId;
  },

  addStoryboardSplitNode: (sourceNodeId, rows, cols, frames, frameAspectRatio) => {
    const state = get();
    const { nodeId, patch } = applyAddStoryboardSplitNode(state, sourceNodeId, rows, cols, frames, frameAspectRatio);
    set(patch);
    return nodeId;
  },

  updateNodeData: (nodeId, data) => {
    set((state) => applyUpdateNodeData(state, nodeId, data));
  },

  updateNodePosition: (nodeId, position) => {
    set((state) => {
      let changed = false;
      const nextNodes = state.nodes.map((node) => {
        if (node.id !== nodeId) {
          return node;
        }

        if (node.position.x === position.x && node.position.y === position.y) {
          return node;
        }

        changed = true;
        return {
          ...node,
          position,
        };
      });

      if (!changed) {
        return {};
      }

      return { nodes: nextNodes };
    });
  },

  updateStoryboardFrame: (nodeId, frameId, data) => {
    set((state) => applyUpdateStoryboardFrame(state, nodeId, frameId, data));
  },

  reorderStoryboardFrame: (nodeId, draggedFrameId, targetFrameId) => {
    set((state) => applyReorderStoryboardFrame(state, nodeId, draggedFrameId, targetFrameId));
  },

  deleteNode: (nodeId) => {
    get().deleteNodes([nodeId]);
  },

  deleteNodes: (nodeIds) => {
    set((state) => applyDeleteNodes(state, nodeIds));
  },

  groupNodes: (nodeIds) => {
    const state = get();
    const { result, patch } = applyGroupNodes(state, nodeIds);
    if (result !== null) {
      set(patch);
    }
    return result;
  },

  ungroupNode: (groupNodeId) => {
    const state = get();
    const { result, patch } = applyUngroupNode(state, groupNodeId);
    if (result) {
      set(patch);
    }
    return result;
  },

  deleteEdge: (edgeId) => {
    set((state) => {
      const hasEdge = state.edges.some((edge) => edge.id === edgeId);
      if (!hasEdge) {
        return {};
      }

      return {
        edges: state.edges.filter((edge) => edge.id !== edgeId),
        history: {
          past: pushSnapshot(state.history.past, createSnapshot(state.nodes, state.edges)),
          future: [],
        },
        dragHistorySnapshot: null,
      };
    });
  },

  setSelectedNode: (nodeId) => {
    set({ selectedNodeId: nodeId });
  },

  openToolDialog: (dialog) => {
    set({ activeToolDialog: dialog });
  },

  closeToolDialog: () => {
    set({ activeToolDialog: null });
  },

  undo: () => {
    const state = get();
    const { success, patch } = applyUndo(state);
    if (success) {
      set(patch);
    }
    return success;
  },

  redo: () => {
    const state = get();
    const { success, patch } = applyRedo(state);
    if (success) {
      set(patch);
    }
    return success;
  },

  clearCanvas: () => {
    set((state) => {
      if (state.nodes.length === 0 && state.edges.length === 0) {
        return {};
      }

      return {
        nodes: [],
        edges: [],
        selectedNodeId: null,
        activeToolDialog: null,
        history: {
          past: pushSnapshot(state.history.past, createSnapshot(state.nodes, state.edges)),
          future: [],
        },
        dragHistorySnapshot: null,
      };
    });
  },
}));
