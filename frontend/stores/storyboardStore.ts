import { create } from 'zustand';
import {
  StoryboardNode,
  StoryboardEdge,
  StoryboardCharacter,
  ProjectSummary,
} from '../types';

// ─── Local types ──────────────────────────────────────────────────────────────

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  actions?: unknown[];
  timestamp: string;
}

export interface ImagePoolEntry {
  url: string;
  previewUrl?: string;
  refCount: number;
  hash?: string;
}

const MAX_HISTORY = 50;

interface CanvasSnapshot {
  nodes: StoryboardNode[];
  edges: StoryboardEdge[];
}

function serializeSnapshot(nodes: StoryboardNode[], edges: StoryboardEdge[]): string {
  return JSON.stringify({ nodes, edges } satisfies CanvasSnapshot);
}

function deserializeSnapshot(snapshot: string): CanvasSnapshot {
  return JSON.parse(snapshot) as CanvasSnapshot;
}

// ─── State & actions interface ────────────────────────────────────────────────

interface StoryboardState {
  // Project list
  projectList: ProjectSummary[];

  // Current project
  currentProjectId: string | null;

  // Canvas
  nodes: StoryboardNode[];
  edges: StoryboardEdge[];
  viewport: { x: number; y: number; zoom: number };
  selectedNodeId: string | null;

  // Characters
  characters: StoryboardCharacter[];

  // History (undo/redo) — JSON snapshots, max 50
  history: { past: string[]; future: string[] };

  // Chat
  chatMessages: ChatMessage[];

  // Timeline ordering (node IDs in sequence)
  timelineOrder: string[];

  // Image pool
  imagePool: Record<string, ImagePoolEntry>;

  // ─── Actions ───────────────────────────────────────────────────────────────

  setProjectList: (list: ProjectSummary[]) => void;
  setCurrentProject: (id: string | null) => void;

  setNodes: (nodes: StoryboardNode[]) => void;
  addNode: (node: StoryboardNode) => void;
  updateNodeData: (nodeId: string, data: Partial<StoryboardNode>) => void;
  deleteNode: (nodeId: string) => void;

  setEdges: (edges: StoryboardEdge[]) => void;

  setViewport: (viewport: { x: number; y: number; zoom: number }) => void;
  setSelectedNodeId: (id: string | null) => void;

  setCharacters: (chars: StoryboardCharacter[]) => void;
  addCharacter: (char: StoryboardCharacter) => void;
  updateCharacter: (id: string, data: Partial<StoryboardCharacter>) => void;
  removeCharacter: (id: string) => void;

  pushHistory: () => void;
  undo: () => void;
  redo: () => void;

  addChatMessage: (msg: ChatMessage) => void;
  clearChat: () => void;

  setTimelineOrder: (order: string[]) => void;

  addToPool: (key: string, entry: ImagePoolEntry) => void;
  removeFromPool: (key: string) => void;

  resetCanvas: () => void;
}

// ─── Store ────────────────────────────────────────────────────────────────────

export const useStoryboardStore = create<StoryboardState>((set, get) => ({
  // Initial state
  projectList: [],
  currentProjectId: null,
  nodes: [],
  edges: [],
  viewport: { x: 0, y: 0, zoom: 1 },
  selectedNodeId: null,
  characters: [],
  history: { past: [], future: [] },
  chatMessages: [],
  timelineOrder: [],
  imagePool: {},

  // ─── Project list ─────────────────────────────────────────────────────────

  setProjectList: (list) => set({ projectList: list }),

  setCurrentProject: (id) =>
    set({
      currentProjectId: id,
      nodes: [],
      edges: [],
      viewport: { x: 0, y: 0, zoom: 1 },
      selectedNodeId: null,
      characters: [],
      history: { past: [], future: [] },
      chatMessages: [],
      timelineOrder: [],
    }),

  // ─── Nodes ────────────────────────────────────────────────────────────────

  setNodes: (nodes) => set({ nodes }),

  addNode: (node) =>
    set((state) => ({ nodes: [...state.nodes, node] })),

  updateNodeData: (nodeId, data) =>
    set((state) => ({
      nodes: state.nodes.map((n) =>
        n.id === nodeId ? { ...n, ...data } : n
      ),
    })),

  deleteNode: (nodeId) =>
    set((state) => ({
      nodes: state.nodes.filter((n) => n.id !== nodeId),
      edges: state.edges.filter(
        (e) => e.source_node_id !== nodeId && e.target_node_id !== nodeId
      ),
      selectedNodeId: state.selectedNodeId === nodeId ? null : state.selectedNodeId,
    })),

  // ─── Edges ────────────────────────────────────────────────────────────────

  setEdges: (edges) => set({ edges }),

  // ─── Viewport & selection ─────────────────────────────────────────────────

  setViewport: (viewport) => set({ viewport }),

  setSelectedNodeId: (id) => set({ selectedNodeId: id }),

  // ─── Characters ───────────────────────────────────────────────────────────

  setCharacters: (chars) => set({ characters: chars }),

  addCharacter: (char) =>
    set((state) => ({ characters: [...state.characters, char] })),

  updateCharacter: (id, data) =>
    set((state) => ({
      characters: state.characters.map((c) =>
        c.id === id ? { ...c, ...data } : c
      ),
    })),

  removeCharacter: (id) =>
    set((state) => ({
      characters: state.characters.filter((c) => c.id !== id),
    })),

  // ─── History ──────────────────────────────────────────────────────────────

  pushHistory: () =>
    set((state) => {
      const snapshot = serializeSnapshot(state.nodes, state.edges);
      const past = [...state.history.past, snapshot].slice(-MAX_HISTORY);
      return { history: { past, future: [] } };
    }),

  undo: () =>
    set((state) => {
      const { past, future } = state.history;
      if (past.length === 0) return state;

      const previous = past[past.length - 1];
      const newPast = past.slice(0, -1);
      const currentSnapshot = serializeSnapshot(state.nodes, state.edges);
      const { nodes, edges } = deserializeSnapshot(previous);

      return {
        nodes,
        edges,
        history: {
          past: newPast,
          future: [currentSnapshot, ...future].slice(0, MAX_HISTORY),
        },
      };
    }),

  redo: () =>
    set((state) => {
      const { past, future } = state.history;
      if (future.length === 0) return state;

      const next = future[0];
      const newFuture = future.slice(1);
      const currentSnapshot = serializeSnapshot(state.nodes, state.edges);
      const { nodes, edges } = deserializeSnapshot(next);

      return {
        nodes,
        edges,
        history: {
          past: [...past, currentSnapshot].slice(-MAX_HISTORY),
          future: newFuture,
        },
      };
    }),

  // ─── Chat ─────────────────────────────────────────────────────────────────

  addChatMessage: (msg) =>
    set((state) => ({ chatMessages: [...state.chatMessages, msg] })),

  clearChat: () => set({ chatMessages: [] }),

  // ─── Timeline ─────────────────────────────────────────────────────────────

  setTimelineOrder: (order) => set({ timelineOrder: order }),

  // ─── Image pool ───────────────────────────────────────────────────────────

  addToPool: (key, entry) =>
    set((state) => ({
      imagePool: { ...state.imagePool, [key]: entry },
    })),

  removeFromPool: (key) =>
    set((state) => {
      const { [key]: _removed, ...rest } = state.imagePool;
      return { imagePool: rest };
    }),

  // ─── Reset ────────────────────────────────────────────────────────────────

  resetCanvas: () =>
    set({
      nodes: [],
      edges: [],
      viewport: { x: 0, y: 0, zoom: 1 },
      selectedNodeId: null,
      history: { past: [], future: [] },
      timelineOrder: [],
    }),
}));
