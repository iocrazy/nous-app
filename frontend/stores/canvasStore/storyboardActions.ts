/**
 * Storyboard frame action implementations for canvasStore.
 * These pure functions are called by the store's set() callbacks.
 */
import {
  type CanvasNode,
  type CanvasEdge,
  type StoryboardFrameItem,
  isStoryboardSplitNode,
} from '../../features/storyboard/domain/canvasNodes';
import { CanvasHistorySnapshot } from './helpers';
import { createSnapshot, pushSnapshot } from './helpers';

interface StoryboardActionState {
  nodes: CanvasNode[];
  edges: CanvasEdge[];
  history: { past: CanvasHistorySnapshot[]; future: CanvasHistorySnapshot[] };
  dragHistorySnapshot: CanvasHistorySnapshot | null;
}

export function applyUpdateStoryboardFrame(
  state: StoryboardActionState,
  nodeId: string,
  frameId: string,
  data: Partial<StoryboardFrameItem>
): Partial<StoryboardActionState> | Record<string, never> {
  let changed = false;
  const nextNodes = state.nodes.map((node) => {
    if (node.id !== nodeId || !isStoryboardSplitNode(node)) {
      return node;
    }

    const nextFrames = node.data.frames.map((frame) => {
      if (frame.id !== frameId) {
        return frame;
      }

      const patchEntries = Object.entries(data) as Array<
        [keyof StoryboardFrameItem, StoryboardFrameItem[keyof StoryboardFrameItem]]
      >;
      const hasFrameChange = patchEntries.some(([key, nextValue]) =>
        !Object.is(frame[key], nextValue)
      );
      if (!hasFrameChange) {
        return frame;
      }

      changed = true;
      return {
        ...frame,
        ...data,
      };
    });

    return {
      ...node,
      data: {
        ...node.data,
        frames: nextFrames,
      },
    };
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

export function applyReorderStoryboardFrame(
  state: StoryboardActionState,
  nodeId: string,
  draggedFrameId: string,
  targetFrameId: string
): Partial<StoryboardActionState> | Record<string, never> {
  let changed = false;
  const nextNodes = state.nodes.map((node) => {
    if (node.id !== nodeId || !isStoryboardSplitNode(node)) {
      return node;
    }

    const frames = [...node.data.frames].sort((a, b) => a.order - b.order);
    const fromIndex = frames.findIndex((frame) => frame.id === draggedFrameId);
    const toIndex = frames.findIndex((frame) => frame.id === targetFrameId);

    if (fromIndex < 0 || toIndex < 0 || fromIndex === toIndex) {
      return node;
    }

    changed = true;
    const [movedFrame] = frames.splice(fromIndex, 1);
    frames.splice(toIndex, 0, movedFrame);

    return {
      ...node,
      data: {
        ...node.data,
        frames: frames.map((frame, index) => ({
          ...frame,
          order: index,
        })),
      },
    };
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
