/**
 * Node change action implementations for canvasStore.
 * Covers onNodesChange (drag/resize history), updateNodeData, deleteNodes, undo, redo.
 */
import {
  NodeChange,
  applyNodeChanges,
} from '@xyflow/react';

import {
  type CanvasEdge,
  type CanvasNode,
  type CanvasNodeData,
} from '../../features/storyboard/domain/canvasNodes';

import {
  type CanvasHistorySnapshot,
  collectNodeIdsWithDescendants,
  createSnapshot,
  isImageAutoResizableType,
  maybeApplyImageAutoResize,
  pushSnapshot,
  resolveActiveToolDialog,
  resolveSelectedNodeId,
  withManualSizeLock,
} from './helpers';

interface ChangeActionState {
  nodes: CanvasNode[];
  edges: CanvasEdge[];
  selectedNodeId: string | null;
  activeToolDialog: { nodeId: string } | null;
  history: { past: CanvasHistorySnapshot[]; future: CanvasHistorySnapshot[] };
  dragHistorySnapshot: CanvasHistorySnapshot | null;
}

export function applyNodesChange(
  state: ChangeActionState,
  changes: NodeChange<CanvasNode>[]
): Partial<ChangeActionState> {
  const resizedNodeIds = new Set(
    changes
      .filter(
        (change): change is NodeChange<CanvasNode> & { id: string } =>
          change.type === 'dimensions'
          && 'resizing' in change
          && change.resizing === false
          && typeof change.id === 'string'
      )
      .map((change) => change.id)
  );

  let nextNodes = applyNodeChanges<CanvasNode>(changes, state.nodes);
  if (resizedNodeIds.size > 0) {
    nextNodes = nextNodes.map((node) => {
      if (!resizedNodeIds.has(node.id) || !isImageAutoResizableType(node.type)) {
        return node;
      }
      return withManualSizeLock(node);
    });
  }

  const hasMeaningfulChange = changes.some((change) => change.type !== 'select');
  const hasDragMove = changes.some(
    (change) =>
      change.type === 'position' &&
      'dragging' in change &&
      Boolean(change.dragging)
  );
  const hasDragEnd = changes.some(
    (change) =>
      change.type === 'position' &&
      'dragging' in change &&
      change.dragging === false
  );
  const hasResizeMove = changes.some(
    (change) =>
      change.type === 'dimensions' &&
      'resizing' in change &&
      Boolean(change.resizing)
  );
  const hasResizeEnd = changes.some(
    (change) =>
      change.type === 'dimensions' &&
      'resizing' in change &&
      change.resizing === false
  );
  const hasInteractionMove = hasDragMove || hasResizeMove;
  const hasInteractionEnd = hasDragEnd || hasResizeEnd;

  let nextHistory = state.history;
  let nextDragHistorySnapshot = state.dragHistorySnapshot;

  if (hasInteractionMove && !nextDragHistorySnapshot) {
    nextDragHistorySnapshot = createSnapshot(state.nodes, state.edges);
  }

  if (hasInteractionEnd) {
    const snapshot = nextDragHistorySnapshot ?? createSnapshot(state.nodes, state.edges);
    nextHistory = {
      past: pushSnapshot(state.history.past, snapshot),
      future: [],
    };
    nextDragHistorySnapshot = null;
  } else if (hasMeaningfulChange && !hasInteractionMove) {
    nextHistory = {
      past: pushSnapshot(state.history.past, createSnapshot(state.nodes, state.edges)),
      future: [],
    };
    nextDragHistorySnapshot = null;
  }

  return {
    nodes: nextNodes,
    selectedNodeId: resolveSelectedNodeId(state.selectedNodeId, nextNodes),
    activeToolDialog: resolveActiveToolDialog(state.activeToolDialog, nextNodes),
    history: nextHistory,
    dragHistorySnapshot: nextDragHistorySnapshot,
  };
}

export function applyUpdateNodeData(
  state: ChangeActionState,
  nodeId: string,
  data: Partial<CanvasNodeData>
): Partial<ChangeActionState> {
  let changed = false;
  const nextNodes = state.nodes.map((node) => {
    if (node.id !== nodeId) {
      return node;
    }

    const hasDataChange = Object.entries(data).some(([key, nextValue]) => {
      const previousValue = (node.data as Record<string, unknown>)[key];
      return !Object.is(previousValue, nextValue);
    });
    if (!hasDataChange) {
      return node;
    }

    const mergedData = {
      ...node.data,
      ...data,
    } as CanvasNodeData;
    const resizedNode = maybeApplyImageAutoResize(
      {
        ...node,
        data: mergedData,
      },
      data
    );

    changed = true;
    return resizedNode;
  });

  if (!changed) {
    return {};
  }

  return {
    nodes: nextNodes,
    history: {
      past: pushSnapshot(state.history.past, createSnapshot(state.nodes, state.edges)),
      future: [],
    },
    dragHistorySnapshot: null,
  };
}

export function applyDeleteNodes(
  state: ChangeActionState,
  nodeIds: string[]
): Partial<ChangeActionState> {
  const uniqueIds = Array.from(new Set(nodeIds.filter((nodeId) => nodeId.trim().length > 0)));
  if (uniqueIds.length === 0) {
    return {};
  }

  const existingIds = uniqueIds.filter((nodeId) => state.nodes.some((node) => node.id === nodeId));
  if (existingIds.length === 0) {
    return {};
  }

  const deleteSet = collectNodeIdsWithDescendants(state.nodes, existingIds);
  const nextNodes = state.nodes.filter((node) => !deleteSet.has(node.id));
  const nextEdges = state.edges.filter(
    (edge) => !deleteSet.has(edge.source) && !deleteSet.has(edge.target)
  );

  return {
    nodes: nextNodes,
    edges: nextEdges,
    selectedNodeId:
      state.selectedNodeId && deleteSet.has(state.selectedNodeId) ? null : state.selectedNodeId,
    activeToolDialog:
      state.activeToolDialog && deleteSet.has(state.activeToolDialog.nodeId)
        ? null
        : state.activeToolDialog,
    history: {
      past: pushSnapshot(state.history.past, createSnapshot(state.nodes, state.edges)),
      future: [],
    },
    dragHistorySnapshot: null,
  };
}

export function applyUndo(
  state: ChangeActionState
): { success: boolean; patch: Partial<ChangeActionState> } {
  const target = state.history.past[state.history.past.length - 1];
  if (!target) {
    return { success: false, patch: {} };
  }

  const currentSnapshot = createSnapshot(state.nodes, state.edges);
  const nextPast = state.history.past.slice(0, -1);

  return {
    success: true,
    patch: {
      nodes: target.nodes,
      edges: target.edges,
      selectedNodeId: resolveSelectedNodeId(state.selectedNodeId, target.nodes),
      activeToolDialog: resolveActiveToolDialog(state.activeToolDialog, target.nodes),
      history: {
        past: nextPast,
        future: pushSnapshot(state.history.future, currentSnapshot),
      },
      dragHistorySnapshot: null,
    },
  };
}

export function applyRedo(
  state: ChangeActionState
): { success: boolean; patch: Partial<ChangeActionState> } {
  const target = state.history.future[state.history.future.length - 1];
  if (!target) {
    return { success: false, patch: {} };
  }

  const currentSnapshot = createSnapshot(state.nodes, state.edges);
  const nextFuture = state.history.future.slice(0, -1);

  return {
    success: true,
    patch: {
      nodes: target.nodes,
      edges: target.edges,
      selectedNodeId: resolveSelectedNodeId(state.selectedNodeId, target.nodes),
      activeToolDialog: resolveActiveToolDialog(state.activeToolDialog, target.nodes),
      history: {
        past: pushSnapshot(state.history.past, currentSnapshot),
        future: nextFuture,
      },
      dragHistorySnapshot: null,
    },
  };
}
