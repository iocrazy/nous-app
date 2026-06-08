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
import { fetchScriptProject } from '../services/scriptService';
import type { ScriptChapter } from '../types';

export interface ChapterNodeData {
  title: string;
  summary: string;
  content: string;
  chapterNumber: number;
  branchLabel?: string;
  branchType?: 'condition' | 'choice';
  isExpanded?: boolean;
  contentJson: Record<string, unknown> | null;
  [key: string]: unknown;
}

export type ScriptNode = Node<ChapterNodeData>;
export type ScriptEdge = Edge;

/** Map server chapter rows → ReactFlow nodes (server holds positions). */
export function mapChaptersToNodes(chapters: ScriptChapter[]): ScriptNode[] {
  return chapters.map((ch) => ({
    id: String(ch.id),
    type: 'chapterNode' as const,
    position: { x: ch.position_x, y: ch.position_y },
    data: {
      title: ch.title ?? '',
      summary: ch.summary ?? '',
      content: ch.content ?? '',
      chapterNumber: ch.chapter_number ?? 0,
      branchLabel: ch.branch_label,
      branchType: ch.branch_type,
      contentJson: null,
    },
    ...(ch.width ? { width: ch.width } : {}),
    ...(ch.height ? { height: ch.height } : {}),
  }));
}

/** Derive parent→child edges from chapter parent links. */
export function mapChaptersToEdges(chapters: ScriptChapter[]): ScriptEdge[] {
  return chapters
    .filter((ch) => ch.parent_chapter_id)
    .map((ch) => ({
      id: `edge-${ch.parent_chapter_id}-${ch.id}`,
      source: String(ch.parent_chapter_id),
      target: String(ch.id),
    }));
}

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

  /** Refetch the script from the server and rebuild the canvas from
   * server state (the source of truth for positions + AI-generated
   * content). Used by the async AI dialogs after a task completes. */
  reloadScript: (scriptId: string) => Promise<void>;

  addChapterNode: (position: { x: number; y: number }, data?: Partial<ChapterNodeData>) => string;
  updateNodeData: (nodeId: string, data: Partial<ChapterNodeData>) => void;
  deleteNode: (nodeId: string) => void;
  setSelectedNode: (nodeId: string | null) => void;

  setViewportState: (viewport: { x: number; y: number; zoom: number }) => void;

  viewMode: 'canvas' | 'grid' | 'list';
  editingNodeId: string | null;
  setViewMode: (mode: 'canvas' | 'grid' | 'list') => void;
  setEditingNodeId: (nodeId: string | null) => void;

  // AI dialog state
  expandDialog: { isOpen: boolean; chapterId: string; title: string; summary: string } | null;
  branchDialog: { isOpen: boolean; chapterId: string; title: string; summary: string } | null;
  openExpandDialog: (chapterId: string, title: string, summary: string) => void;
  closeExpandDialog: () => void;
  openBranchDialog: (chapterId: string, title: string, summary: string) => void;
  closeBranchDialog: () => void;

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
  viewMode: 'canvas',
  editingNodeId: null,
  expandDialog: null,
  branchDialog: null,

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

  reloadScript: async (scriptId) => {
    const project = await fetchScriptProject(scriptId);
    set({
      nodes: mapChaptersToNodes(project.chapters),
      edges: mapChaptersToEdges(project.chapters),
      history: { past: [], future: [] },
    });
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
        contentJson: data?.contentJson ?? null,
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

  setViewMode: (mode) => set({ viewMode: mode }),
  setEditingNodeId: (nodeId) => set({ editingNodeId: nodeId }),

  openExpandDialog: (chapterId, title, summary) =>
    set({ expandDialog: { isOpen: true, chapterId, title, summary } }),
  closeExpandDialog: () => set({ expandDialog: null }),
  openBranchDialog: (chapterId, title, summary) =>
    set({ branchDialog: { isOpen: true, chapterId, title, summary } }),
  closeBranchDialog: () => set({ branchDialog: null }),

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
