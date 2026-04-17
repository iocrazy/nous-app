/**
 * Derived node add action implementations for canvasStore.
 * These functions compute new nodes from source nodes and return store patches.
 */
import {
  CANVAS_NODE_TYPES,
  DEFAULT_ASPECT_RATIO,
  EXPORT_RESULT_NODE_MIN_HEIGHT,
  EXPORT_RESULT_NODE_MIN_WIDTH,
  type ActiveToolDialog,
  type CanvasEdge,
  type CanvasNode,
  type CanvasNodeData,
  type ExportImageNodeResultKind,
  type StoryboardFrameItem,
} from '../../features/storyboard/domain/canvasNodes';
import { EXPORT_RESULT_DISPLAY_NAME } from '../../features/storyboard/domain/nodeDisplay';
import { canvasNodeFactory } from '../../features/storyboard/application/canvasServices';
import {
  type CanvasHistorySnapshot,
  type FindNodePositionContext,
  createSnapshot,
  pushSnapshot,
  getDerivedNodePosition,
  resolveDerivedAspectRatio,
  resolveAutoImageNodeDimensions,
  resolveGeneratedImageNodeDimensions,
  computeNodePosition,
  getNodeSize,
  createDefaultStoryboardExportOptions,
} from './helpers';

interface AddNodeActionState {
  nodes: CanvasNode[];
  edges: CanvasEdge[];
  selectedNodeId: string | null;
  activeToolDialog: ActiveToolDialog | null;
  history: { past: CanvasHistorySnapshot[]; future: CanvasHistorySnapshot[] };
  dragHistorySnapshot: CanvasHistorySnapshot | null;
  currentViewport: { x: number; y: number; zoom: number };
  canvasViewportSize: { width: number; height: number };
}

type AddNodePatch = {
  nodes: CanvasNode[];
  selectedNodeId: string;
  activeToolDialog: null;
  history: { past: CanvasHistorySnapshot[]; future: never[] };
  dragHistorySnapshot: null;
};

export function applyAddDerivedUploadNode(
  state: AddNodeActionState,
  sourceNodeId: string,
  imageUrl: string,
  aspectRatio: string,
  previewImageUrl?: string
): { nodeId: string; patch: AddNodePatch } {
  const position = getDerivedNodePosition(state.nodes, sourceNodeId);
  const sourceNode = state.nodes.find((node) => node.id === sourceNodeId);
  const resolvedAspectRatio = resolveDerivedAspectRatio(sourceNode, aspectRatio);
  const node = canvasNodeFactory.createNode(CANVAS_NODE_TYPES.upload, position, {
    imageUrl,
    previewImageUrl: previewImageUrl ?? null,
    aspectRatio: resolvedAspectRatio,
  });
  const derivedSize = resolveGeneratedImageNodeDimensions(resolvedAspectRatio);
  node.width = derivedSize.width;
  node.height = derivedSize.height;
  node.style = {
    ...(node.style ?? {}),
    width: derivedSize.width,
    height: derivedSize.height,
  };

  return {
    nodeId: node.id,
    patch: {
      nodes: [...state.nodes, node],
      selectedNodeId: node.id,
      activeToolDialog: null,
      history: {
        past: pushSnapshot(state.history.past, createSnapshot(state.nodes, state.edges)),
        future: [],
      },
      dragHistorySnapshot: null,
    },
  };
}

export function applyAddDerivedExportNode(
  state: AddNodeActionState,
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
): { nodeId: string; patch: AddNodePatch } {
  const sourceNode = state.nodes.find((node) => node.id === sourceNodeId);
  const aspectRatioStrategy = options?.aspectRatioStrategy ?? 'provided';
  const resolvedAspectRatio = aspectRatioStrategy === 'derivedFromSource'
    ? resolveDerivedAspectRatio(sourceNode, aspectRatio)
    : (aspectRatio || resolveDerivedAspectRatio(sourceNode, DEFAULT_ASPECT_RATIO));
  const autoSize = resolveAutoImageNodeDimensions(resolvedAspectRatio, {
    minWidth: EXPORT_RESULT_NODE_MIN_WIDTH,
    minHeight: EXPORT_RESULT_NODE_MIN_HEIGHT,
  });
  const generatedSize = resolveGeneratedImageNodeDimensions(resolvedAspectRatio, {
    minWidth: EXPORT_RESULT_NODE_MIN_WIDTH,
    minHeight: EXPORT_RESULT_NODE_MIN_HEIGHT,
  });
  const sourceSize = sourceNode ? getNodeSize(sourceNode) : null;
  const sizeStrategy = options?.sizeStrategy
    ?? (options?.matchSourceNodeSize ? 'matchSource' : 'generated');
  let derivedSize = generatedSize;
  if (sizeStrategy === 'autoMinEdge') {
    derivedSize = autoSize;
  } else if (sizeStrategy === 'matchSource' && sourceSize) {
    derivedSize = {
      width: Math.max(1, Math.round(sourceSize.width)),
      height: Math.max(1, Math.round(sourceSize.height)),
    };
  }

  const posCtx: FindNodePositionContext = {
    nodes: state.nodes,
    currentViewport: state.currentViewport,
    canvasViewportSize: state.canvasViewportSize,
  };
  const position = computeNodePosition(posCtx, sourceNodeId, derivedSize.width, derivedSize.height);

  const exportNodeData: Partial<CanvasNodeData> = {
    imageUrl,
    previewImageUrl: previewImageUrl ?? null,
    aspectRatio: resolvedAspectRatio,
  };
  if (options?.defaultTitle) {
    (exportNodeData as { displayName?: string }).displayName = options.defaultTitle;
  }
  if (options?.resultKind) {
    (exportNodeData as { resultKind?: ExportImageNodeResultKind }).resultKind = options.resultKind;
    if (!options.defaultTitle) {
      (exportNodeData as { displayName?: string }).displayName =
        EXPORT_RESULT_DISPLAY_NAME[options.resultKind];
    }
  }
  const node = canvasNodeFactory.createNode(CANVAS_NODE_TYPES.exportImage, position, {
    ...exportNodeData,
  });
  node.width = derivedSize.width;
  node.height = derivedSize.height;
  node.style = {
    ...(node.style ?? {}),
    width: derivedSize.width,
    height: derivedSize.height,
  };

  return {
    nodeId: node.id,
    patch: {
      nodes: [...state.nodes, node],
      selectedNodeId: node.id,
      activeToolDialog: null,
      history: {
        past: pushSnapshot(state.history.past, createSnapshot(state.nodes, state.edges)),
        future: [],
      },
      dragHistorySnapshot: null,
    },
  };
}

export function applyAddStoryboardSplitNode(
  state: AddNodeActionState,
  sourceNodeId: string,
  rows: number,
  cols: number,
  frames: StoryboardFrameItem[],
  frameAspectRatio?: string
): { nodeId: string; patch: AddNodePatch } {
  const position = getDerivedNodePosition(state.nodes, sourceNodeId);
  const resolvedFrameAspectRatio =
    frameAspectRatio ??
    frames.find((frame) => typeof frame.aspectRatio === 'string')?.aspectRatio ??
    DEFAULT_ASPECT_RATIO;

  const node = canvasNodeFactory.createNode(CANVAS_NODE_TYPES.storyboardSplit, position, {
    gridRows: rows,
    gridCols: cols,
    frames,
    aspectRatio: resolvedFrameAspectRatio,
    frameAspectRatio: resolvedFrameAspectRatio,
    exportOptions: createDefaultStoryboardExportOptions(),
  });

  return {
    nodeId: node.id,
    patch: {
      nodes: [...state.nodes, node],
      selectedNodeId: node.id,
      activeToolDialog: null,
      history: {
        past: pushSnapshot(state.history.past, createSnapshot(state.nodes, state.edges)),
        future: [],
      },
      dragHistorySnapshot: null,
    },
  };
}
