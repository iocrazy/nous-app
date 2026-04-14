/**
 * Node group/ungroup action implementations for canvasStore.
 * These pure functions are called by the store's set()/get() callbacks.
 */
import {
  CANVAS_NODE_TYPES,
  type ActiveToolDialog,
  type CanvasEdge,
  type CanvasNode,
} from '../../features/storyboard/domain/canvasNodes';
import { canvasNodeFactory } from '../../features/storyboard/application/canvasServices';
import {
  type CanvasHistorySnapshot,
  createSnapshot,
  pushSnapshot,
  resolveAbsolutePosition,
  getNodeSize,
} from './helpers';

interface GroupActionState {
  nodes: CanvasNode[];
  edges: CanvasEdge[];
  selectedNodeId: string | null;
  activeToolDialog: ActiveToolDialog | null;
  history: { past: CanvasHistorySnapshot[]; future: CanvasHistorySnapshot[] };
  dragHistorySnapshot: CanvasHistorySnapshot | null;
}

export function applyGroupNodes(
  state: GroupActionState,
  nodeIds: string[]
): { result: string | null; patch: Partial<GroupActionState> } {
  const uniqueIds = Array.from(new Set(nodeIds.filter((nodeId) => nodeId.trim().length > 0)));
  if (uniqueIds.length < 2) {
    return { result: null, patch: {} };
  }

  const nodeMap = new Map(state.nodes.map((node) => [node.id, node] as const));
  const existingIds = uniqueIds.filter((nodeId) => nodeMap.has(nodeId));
  if (existingIds.length < 2) {
    return { result: null, patch: {} };
  }

  const selectedSet = new Set(existingIds);
  const memberIds = existingIds.filter((nodeId) => {
    let currentParentId = nodeMap.get(nodeId)?.parentId;
    const visited = new Set<string>();
    while (currentParentId && !visited.has(currentParentId)) {
      if (selectedSet.has(currentParentId)) {
        return false;
      }
      visited.add(currentParentId);
      currentParentId = nodeMap.get(currentParentId)?.parentId;
    }
    return true;
  });
  if (memberIds.length < 2) {
    return { result: null, patch: {} };
  }

  const memberSet = new Set(memberIds);
  const members = memberIds
    .map((id) => nodeMap.get(id))
    .filter((node): node is CanvasNode => Boolean(node));

  const absoluteBounds = members.reduce(
    (acc, node) => {
      const absolute = resolveAbsolutePosition(node, nodeMap);
      const size = getNodeSize(node);
      return {
        minX: Math.min(acc.minX, absolute.x),
        minY: Math.min(acc.minY, absolute.y),
        maxX: Math.max(acc.maxX, absolute.x + size.width),
        maxY: Math.max(acc.maxY, absolute.y + size.height),
      };
    },
    {
      minX: Number.POSITIVE_INFINITY,
      minY: Number.POSITIVE_INFINITY,
      maxX: Number.NEGATIVE_INFINITY,
      maxY: Number.NEGATIVE_INFINITY,
    }
  );

  if (!Number.isFinite(absoluteBounds.minX) || !Number.isFinite(absoluteBounds.minY)) {
    return { result: null, patch: {} };
  }

  const SIDE_PADDING = 20;
  const TOP_PADDING = 34;
  const BOTTOM_PADDING = 20;
  const groupX = Math.round(absoluteBounds.minX - SIDE_PADDING);
  const groupY = Math.round(absoluteBounds.minY - TOP_PADDING);
  const groupWidth = Math.round(
    Math.max(220, absoluteBounds.maxX - absoluteBounds.minX + SIDE_PADDING * 2)
  );
  const groupHeight = Math.round(
    Math.max(140, absoluteBounds.maxY - absoluteBounds.minY + TOP_PADDING + BOTTOM_PADDING)
  );

  const existingGroupCount = state.nodes.filter((node) => node.type === CANVAS_NODE_TYPES.group).length;
  const groupDisplayName = `Group ${existingGroupCount + 1}`;
  const groupNode = canvasNodeFactory.createNode(
    CANVAS_NODE_TYPES.group,
    { x: groupX, y: groupY },
    {
      label: groupDisplayName,
      displayName: groupDisplayName,
    }
  );
  groupNode.style = { width: groupWidth, height: groupHeight };
  groupNode.selected = true;

  const updatedMemberMap = new Map<string, CanvasNode>();
  for (const node of members) {
    const absolute = resolveAbsolutePosition(node, nodeMap);
    updatedMemberMap.set(node.id, {
      ...node,
      parentId: groupNode.id,
      extent: 'parent',
      position: {
        x: Math.round(absolute.x - groupX),
        y: Math.round(absolute.y - groupY),
      },
      selected: false,
    });
  }

  const firstMemberIndex = state.nodes.reduce((acc, node, index) => {
    if (!memberSet.has(node.id)) {
      return acc;
    }
    return acc === -1 ? index : Math.min(acc, index);
  }, -1);

  const nextNodes: CanvasNode[] = [];
  let insertedGroup = false;
  for (let index = 0; index < state.nodes.length; index += 1) {
    const node = state.nodes[index];
    if (!insertedGroup && index === firstMemberIndex) {
      nextNodes.push(groupNode);
      insertedGroup = true;
    }

    const updatedMember = updatedMemberMap.get(node.id);
    if (updatedMember) {
      nextNodes.push(updatedMember);
    } else {
      nextNodes.push({
        ...node,
        selected: false,
      });
    }
  }

  if (!insertedGroup) {
    nextNodes.push(groupNode);
  }

  return {
    result: groupNode.id,
    patch: {
      nodes: nextNodes,
      selectedNodeId: groupNode.id,
      activeToolDialog:
        state.activeToolDialog && memberSet.has(state.activeToolDialog.nodeId)
          ? null
          : state.activeToolDialog,
      history: {
        past: pushSnapshot(state.history.past, createSnapshot(state.nodes, state.edges)),
        future: [],
      },
      dragHistorySnapshot: null,
    },
  };
}

export function applyUngroupNode(
  state: GroupActionState,
  groupNodeId: string
): { result: boolean; patch: Partial<GroupActionState> } {
  const groupNode = state.nodes.find(
    (node) => node.id === groupNodeId && node.type === CANVAS_NODE_TYPES.group
  );
  if (!groupNode) {
    return { result: false, patch: {} };
  }

  const nodeMap = new Map(state.nodes.map((node) => [node.id, node] as const));
  const children = state.nodes.filter((node) => node.parentId === groupNodeId);
  if (children.length === 0) {
    return { result: false, patch: {} };
  }

  const nextNodes = state.nodes
    .filter((node) => node.id !== groupNodeId)
    .map((node) => {
      if (node.parentId !== groupNodeId) {
        return node;
      }

      const absolute = resolveAbsolutePosition(node, nodeMap);
      return {
        ...node,
        parentId: undefined,
        extent: undefined,
        position: {
          x: Math.round(absolute.x),
          y: Math.round(absolute.y),
        },
        selected: false,
      };
    });

  const nextEdges = state.edges.filter(
    (edge) => edge.source !== groupNodeId && edge.target !== groupNodeId
  );

  return {
    result: true,
    patch: {
      nodes: nextNodes,
      edges: nextEdges,
      selectedNodeId: state.selectedNodeId === groupNodeId ? null : state.selectedNodeId,
      activeToolDialog:
        state.activeToolDialog?.nodeId === groupNodeId ? null : state.activeToolDialog,
      history: {
        past: pushSnapshot(state.history.past, createSnapshot(state.nodes, state.edges)),
        future: [],
      },
      dragHistorySnapshot: null,
    },
  };
}
