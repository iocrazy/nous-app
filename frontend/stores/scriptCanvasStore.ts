import { create } from 'zustand';
import {
  type Node,
  type Edge,
  type NodeChange,
  type EdgeChange,
  type Connection,
  applyNodeChanges,
  applyEdgeChanges,
  addEdge as addReactFlowEdge,
} from '@xyflow/react';

export interface ChapterNodeData {
  title: string;
  summary: string;
  content: string;
  chapterNumber: number;
  branchLabel?: string;
  branchType?: 'condition' | 'choice';
  isExpanded?: boolean;
  [key: string]: unknown;
}

export type ScriptNode = Node<ChapterNodeData>;
export type ScriptEdge = Edge;

interface HistorySnapshot {
  nodes: ScriptNode[];
  edges: ScriptEdge[];
}

const MAX_HISTORY = 50;

interface ScriptCanvasState {
  nodes: ScriptNode[];
  edges: ScriptEdge[];
  selectedNodeId: string | null;
  history: { past: HistorySnapshot[]; future: HistorySnapshot[] };
  currentViewport: { x: number; y: number; zoom: number };

  onNodesChange: (changes: NodeChange<ScriptNode>[]) => void;
  onEdgesChange: (changes: EdgeChange<ScriptEdge>[]) => void;
  onConnect: (connection: Connection) => void;

  setCanvasData: (nodes: ScriptNode[], edges: ScriptEdge[]) => void;
  clearCanvas: () => void;

  addChapterNode: (position: { x: number; y: number }, data?: Partial<ChapterNodeData>) => string;
  updateNodeData: (nodeId: string, data: Partial<ChapterNodeData>) => void;
  deleteNode: (nodeId: string) => void;
  setSelectedNode: (nodeId: string | null) => void;

  setViewportState: (viewport: { x: number; y: number; zoom: number }) => void;

  undo: () => boolean;
  redo: () => boolean;
}

function pushHistory(state: ScriptCanvasState): { past: HistorySnapshot[]; future: HistorySnapshot[] } {
  const snapshot: HistorySnapshot = {
    nodes: JSON.parse(JSON.stringify(state.nodes)),
    edges: JSON.parse(JSON.stringify(state.edges)),
  };
  const past = [...state.history.past, snapshot].slice(-MAX_HISTORY);
  return { past, future: [] };
}

export const useScriptCanvasStore = create<ScriptCanvasState>((set, get) => ({
  nodes: [],
  edges: [],
  selectedNodeId: null,
  history: { past: [], future: [] },
  currentViewport: { x: 0, y: 0, zoom: 1 },

  onNodesChange: (changes) => {
    set((state) => ({
      nodes: applyNodeChanges(changes, state.nodes),
    }));
  },

  onEdgesChange: (changes) => {
    set((state) => ({
      edges: applyEdgeChanges(changes, state.edges),
    }));
  },

  onConnect: (connection) => {
    set((state) => ({
      edges: addReactFlowEdge(connection, state.edges),
      history: pushHistory(state),
    }));
  },

  setCanvasData: (nodes, edges) => {
    set({ nodes, edges, history: { past: [], future: [] } });
  },

  clearCanvas: () => {
    set((state) => ({
      nodes: [],
      edges: [],
      selectedNodeId: null,
      history: pushHistory(state),
    }));
  },

  addChapterNode: (position, data) => {
    const id = crypto.randomUUID();
    const chapterCount = get().nodes.length;
    const newNode: ScriptNode = {
      id,
      type: 'chapterNode',
      position,
      data: {
        title: data?.title ?? `Chapter ${chapterCount + 1}`,
        summary: data?.summary ?? '',
        content: data?.content ?? '',
        chapterNumber: data?.chapterNumber ?? chapterCount + 1,
        branchLabel: data?.branchLabel,
        branchType: data?.branchType,
        ...data,
      },
    };
    set((state) => ({
      nodes: [...state.nodes, newNode],
      history: pushHistory(state),
    }));
    return id;
  },

  updateNodeData: (nodeId, data) => {
    set((state) => ({
      nodes: state.nodes.map((n) =>
        n.id === nodeId ? { ...n, data: { ...n.data, ...data } } : n,
      ),
      history: pushHistory(state),
    }));
  },

  deleteNode: (nodeId) => {
    set((state) => ({
      nodes: state.nodes.filter((n) => n.id !== nodeId),
      edges: state.edges.filter((e) => e.source !== nodeId && e.target !== nodeId),
      selectedNodeId: state.selectedNodeId === nodeId ? null : state.selectedNodeId,
      history: pushHistory(state),
    }));
  },

  setSelectedNode: (nodeId) => set({ selectedNodeId: nodeId }),

  setViewportState: (viewport) => set({ currentViewport: viewport }),

  undo: () => {
    const { history, nodes, edges } = get();
    if (history.past.length === 0) return false;
    const prev = history.past[history.past.length - 1];
    set({
      nodes: prev.nodes,
      edges: prev.edges,
      history: {
        past: history.past.slice(0, -1),
        future: [{ nodes: JSON.parse(JSON.stringify(nodes)), edges: JSON.parse(JSON.stringify(edges)) }, ...history.future],
      },
    });
    return true;
  },

  redo: () => {
    const { history, nodes, edges } = get();
    if (history.future.length === 0) return false;
    const next = history.future[0];
    set({
      nodes: next.nodes,
      edges: next.edges,
      history: {
        past: [...history.past, { nodes: JSON.parse(JSON.stringify(nodes)), edges: JSON.parse(JSON.stringify(edges)) }],
        future: history.future.slice(1),
      },
    });
    return true;
  },
}));
