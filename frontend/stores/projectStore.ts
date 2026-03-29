import { create } from 'zustand';
import { v4 as uuidv4 } from 'uuid';
import type { Viewport } from '@xyflow/react';
import {
  useCanvasStore,
  type CanvasEdge,
  type CanvasHistoryState,
  type CanvasNode,
  type CanvasNodeData,
} from './canvasStore';
import {
  fetchProject,
  fetchProjects,
  createProject as apiCreateProject,
  updateProject as apiUpdateProject,
  deleteProject as apiDeleteProject,
  updateViewport as apiUpdateViewport,
  syncCanvas,
  type ProjectFull,
} from '../services/storyboardService';
import { useStoryboardStore } from './storyboardStore';

// ─── Constants ──────────────────────────────────────────────────────────────

const DEFAULT_VIEWPORT: Viewport = { x: 0, y: 0, zoom: 1 };
const IMAGE_REF_PREFIX = '__img_ref__:';
const UPSERT_DEBOUNCE_MS = 800;
const VIEWPORT_UPSERT_DEBOUNCE_MS = 1200;
const VIEWPORT_EPSILON = 0.001;
const MAX_PERSISTED_HISTORY_STEPS = 12;

function createEmptyHistory(): CanvasHistoryState {
  return { past: [], future: [] };
}

// ─── Image Pool Encoding / Decoding ─────────────────────────────────────────

export function encodeImageReference(
  imageUrl: string | null | undefined,
  imagePool: string[],
  imageIndexMap: Map<string, number>
): string | null | undefined {
  if (typeof imageUrl !== 'string' || imageUrl.length === 0) {
    return imageUrl;
  }

  const existingIndex = imageIndexMap.get(imageUrl);
  if (typeof existingIndex === 'number') {
    return `${IMAGE_REF_PREFIX}${existingIndex}`;
  }

  const nextIndex = imagePool.length;
  imagePool.push(imageUrl);
  imageIndexMap.set(imageUrl, nextIndex);
  return `${IMAGE_REF_PREFIX}${nextIndex}`;
}

export function decodeImageReference(
  imageUrl: string | null | undefined,
  imagePool: string[] | undefined
): string | null | undefined {
  if (typeof imageUrl !== 'string' || !imagePool || !imageUrl.startsWith(IMAGE_REF_PREFIX)) {
    return imageUrl;
  }

  const index = Number.parseInt(imageUrl.slice(IMAGE_REF_PREFIX.length), 10);
  if (!Number.isFinite(index) || index < 0) {
    return imageUrl;
  }

  return imagePool[index] ?? null;
}

// ─── Node Image Reference Mapping ───────────────────────────────────────────

function mapNodeImageReferences(
  nodes: CanvasNode[],
  mapImageUrl: (imageUrl: string | null | undefined) => string | null | undefined
): CanvasNode[] {
  return nodes.map((node) => {
    const nodeData = node.data as Record<string, unknown>;
    const nextData: Record<string, unknown> = { ...nodeData };

    if ('imageUrl' in nextData) {
      nextData.imageUrl = mapImageUrl(nextData.imageUrl as string | null | undefined) ?? null;
    }
    if ('previewImageUrl' in nextData) {
      nextData.previewImageUrl =
        mapImageUrl(nextData.previewImageUrl as string | null | undefined) ?? null;
    }

    if (Array.isArray(nextData.frames)) {
      nextData.frames = nextData.frames.map((frame: Record<string, unknown>) => {
        if (!frame || typeof frame !== 'object') {
          return frame;
        }

        if (!('imageUrl' in frame)) {
          return frame;
        }

        return {
          ...frame,
          imageUrl: mapImageUrl(frame.imageUrl as string | null | undefined) ?? null,
          previewImageUrl:
            mapImageUrl(frame.previewImageUrl as string | null | undefined) ?? null,
        };
      });
    }

    return {
      ...node,
      data: nextData as CanvasNodeData,
    };
  });
}

function mapHistoryImageReferences(
  history: CanvasHistoryState,
  mapImageUrl: (imageUrl: string | null | undefined) => string | null | undefined
): CanvasHistoryState {
  return {
    past: history.past.map((snapshot) => ({
      ...snapshot,
      nodes: mapNodeImageReferences(snapshot.nodes, mapImageUrl),
    })),
    future: history.future.map((snapshot) => ({
      ...snapshot,
      nodes: mapNodeImageReferences(snapshot.nodes, mapImageUrl),
    })),
  };
}

function trimHistoryForPersistence(history: CanvasHistoryState): CanvasHistoryState {
  return {
    past: history.past.slice(-MAX_PERSISTED_HISTORY_STEPS),
    future: history.future.slice(-MAX_PERSISTED_HISTORY_STEPS),
  };
}

// ─── Project Serialization ──────────────────────────────────────────────────

export interface ProjectSerialized {
  nodes: CanvasNode[];
  edges: CanvasEdge[];
  history: CanvasHistoryState;
  imagePool: string[];
}

export function serializeProject(
  nodes: CanvasNode[],
  edges: CanvasEdge[],
  history: CanvasHistoryState
): ProjectSerialized {
  const imagePool: string[] = [];
  const imageIndexMap = new Map<string, number>();
  const encode = (url: string | null | undefined) =>
    encodeImageReference(url, imagePool, imageIndexMap);

  const trimmedHistory = trimHistoryForPersistence(history);

  return {
    nodes: mapNodeImageReferences(nodes, encode),
    edges,
    history: mapHistoryImageReferences(trimmedHistory, encode),
    imagePool,
  };
}

export function deserializeProject(
  serialized: ProjectSerialized
): { nodes: CanvasNode[]; edges: CanvasEdge[]; history: CanvasHistoryState } {
  const decode = (url: string | null | undefined) =>
    decodeImageReference(url, serialized.imagePool);

  return {
    nodes: mapNodeImageReferences(serialized.nodes, decode),
    edges: serialized.edges,
    history: mapHistoryImageReferences(serialized.history, decode),
  };
}

// ─── Viewport Helpers ───────────────────────────────────────────────────────

function hasViewportMeaningfulDelta(current: Viewport, next: Viewport): boolean {
  return (
    Math.abs(current.x - next.x) > VIEWPORT_EPSILON ||
    Math.abs(current.y - next.y) > VIEWPORT_EPSILON ||
    Math.abs(current.zoom - next.zoom) > VIEWPORT_EPSILON
  );
}

function normalizeViewport(viewport: Viewport): Viewport {
  return {
    x: Number(viewport.x.toFixed(2)),
    y: Number(viewport.y.toFixed(2)),
    zoom: Number(viewport.zoom.toFixed(4)),
  };
}

// ─── Debounced Persist Queue ────────────────────────────────────────────────

interface PersistQueue {
  syncTimers: Map<string, ReturnType<typeof setTimeout>>;
  viewportTimers: Map<string, ReturnType<typeof setTimeout>>;
  syncInFlight: Set<string>;
  viewportInFlight: Set<string>;
  deletingIds: Set<string>;
}

const queue: PersistQueue = {
  syncTimers: new Map(),
  viewportTimers: new Map(),
  syncInFlight: new Set(),
  viewportInFlight: new Set(),
  deletingIds: new Set(),
};

function clearSyncTimer(projectId: string): void {
  const timer = queue.syncTimers.get(projectId);
  if (timer) {
    clearTimeout(timer);
    queue.syncTimers.delete(projectId);
  }
}

function clearViewportTimer(projectId: string): void {
  const timer = queue.viewportTimers.get(projectId);
  if (timer) {
    clearTimeout(timer);
    queue.viewportTimers.delete(projectId);
  }
}

// ─── Summary Helpers ────────────────────────────────────────────────────────

export interface ProjectSummary {
  id: string;
  name: string;
  createdAt: number;
  updatedAt: number;
  nodeCount: number;
}

function updateProjectSummary(
  summaries: ProjectSummary[],
  updated: ProjectSummary
): ProjectSummary[] {
  const exists = summaries.some((s) => s.id === updated.id);
  const next = exists
    ? summaries.map((s) => (s.id === updated.id ? updated : s))
    : [updated, ...summaries];
  return [...next].sort((a, b) => b.updatedAt - a.updatedAt);
}

// ─── Project Interface ──────────────────────────────────────────────────────

export interface Project extends ProjectSummary {
  nodes: CanvasNode[];
  edges: CanvasEdge[];
  viewport: Viewport;
  history: CanvasHistoryState;
  teamId: string;
  isDirty: boolean;
}

// ─── Store ──────────────────────────────────────────────────────────────────

let openProjectRequestSeq = 0;

interface ProjectState {
  projects: ProjectSummary[];
  currentProjectId: string | null;
  currentProject: Project | null;
  isHydrated: boolean;
  isOpeningProject: boolean;

  hydrate: (teamId: string) => Promise<void>;
  createProject: (teamId: string, name: string) => Promise<string | null>;
  deleteProject: (id: string) => void;
  renameProject: (id: string, name: string) => void;
  openProject: (id: string) => void;
  closeProject: () => void;
  getCurrentProject: () => Project | null;
  saveCurrentProject: (
    nodes: CanvasNode[],
    edges: CanvasEdge[],
    viewport?: Viewport,
    history?: CanvasHistoryState
  ) => void;
  saveCurrentProjectViewport: (viewport: Viewport) => void;
  cancelPendingViewportPersist: () => void;
  markDirty: () => void;
  markClean: () => void;
}

export const useProjectStore = create<ProjectState>((set, get) => ({
  projects: [],
  currentProjectId: null,
  currentProject: null,
  isHydrated: false,
  isOpeningProject: false,

  hydrate: async (teamId: string) => {
    try {
      const result = await fetchProjects(teamId);
      const projects: ProjectSummary[] = result.data.map((p) => ({
        id: p.id,
        name: p.name,
        createdAt: new Date(p.created_at ?? Date.now()).getTime(),
        updatedAt: new Date(p.updated_at ?? Date.now()).getTime(),
        nodeCount: p.node_count ?? 0,
      }));
      projects.sort((a, b) => b.updatedAt - a.updatedAt);
      set({
        projects,
        currentProjectId: null,
        currentProject: null,
        isHydrated: true,
      });
    } catch (error) {
      console.error('Failed to hydrate project summaries', error);
      set({
        projects: [],
        currentProjectId: null,
        currentProject: null,
        isHydrated: true,
      });
    }
  },

  createProject: async (teamId: string, name: string) => {
    try {
      const created = await apiCreateProject({ team_id: teamId, name });
      const now = Date.now();
      const project: Project = {
        id: created.id,
        name: created.name ?? name,
        createdAt: now,
        updatedAt: now,
        nodeCount: 0,
        nodes: [],
        edges: [],
        viewport: DEFAULT_VIEWPORT,
        history: createEmptyHistory(),
        teamId,
        isDirty: false,
      };

      set((state) => ({
        projects: [
          { id: project.id, name: project.name, createdAt: project.createdAt, updatedAt: project.updatedAt, nodeCount: 0 },
          ...state.projects,
        ],
        currentProjectId: project.id,
        currentProject: project,
        isOpeningProject: false,
      }));

      useStoryboardStore.getState().setCurrentProject(project.id);
      return project.id;
    } catch (error) {
      console.error('Failed to create project', error);
      return null;
    }
  },

  deleteProject: (id: string) => {
    queue.deletingIds.add(id);
    clearSyncTimer(id);
    clearViewportTimer(id);

    set((state) => ({
      projects: state.projects.filter((p) => p.id !== id),
      currentProjectId: state.currentProjectId === id ? null : state.currentProjectId,
      currentProject: state.currentProject?.id === id ? null : state.currentProject,
      isOpeningProject: false,
    }));

    void apiDeleteProject(id)
      .catch((error) => console.error('Failed to delete project', error))
      .finally(() => queue.deletingIds.delete(id));
  },

  renameProject: (id: string, name: string) => {
    const now = Date.now();

    set((state) => {
      const projects = state.projects.map((s) =>
        s.id === id ? { ...s, name, updatedAt: now } : s
      );

      return {
        projects: projects.sort((a, b) => b.updatedAt - a.updatedAt),
        currentProject:
          state.currentProject?.id === id
            ? { ...state.currentProject, name, updatedAt: now }
            : state.currentProject,
      };
    });

    void apiUpdateProject(id, { name }).catch((error) => {
      console.error('Failed to rename project', error);
    });
  },

  openProject: (id: string) => {
    const reqSeq = ++openProjectRequestSeq;
    useCanvasStore.getState().closeImageViewer();
    set({ isOpeningProject: true });

    void (async () => {
      try {
        const record = await fetchProject(id);
        if (reqSeq !== openProjectRequestSeq) return;

        const nodes: CanvasNode[] = (record.nodes ?? []).map((n) => ({
          id: n.id,
          type: (n.data_json as Record<string, unknown>)?.nodeType as string ?? 'upload',
          position: { x: n.position_x ?? 0, y: n.position_y ?? 0 },
          data: (n.data_json ?? {}) as CanvasNodeData,
          width: n.width ?? undefined,
          height: n.height ?? undefined,
        }));

        const edges: CanvasEdge[] = (record.edges ?? []).map((e) => ({
          id: e.id,
          source: e.source_node_id,
          target: e.target_node_id,
          sourceHandle: e.source_handle ?? 'source',
          targetHandle: e.target_handle ?? 'target',
          type: 'disconnectable',
        }));

        const viewport = (record.viewport_json as Viewport) ?? DEFAULT_VIEWPORT;

        const project: Project = {
          id: record.id,
          name: record.name ?? 'Untitled',
          createdAt: new Date(record.created_at ?? Date.now()).getTime(),
          updatedAt: new Date(record.updated_at ?? Date.now()).getTime(),
          nodeCount: nodes.length,
          nodes,
          edges,
          viewport,
          history: createEmptyHistory(),
          teamId: record.team_id ?? '',
          isDirty: false,
        };

        useCanvasStore.getState().setCanvasData(nodes, edges, createEmptyHistory());
        useStoryboardStore.getState().setCurrentProject(id);

        set((state) => ({
          currentProjectId: id,
          currentProject: project,
          isOpeningProject: false,
          projects: updateProjectSummary(state.projects, {
            id: project.id,
            name: project.name,
            createdAt: project.createdAt,
            updatedAt: project.updatedAt,
            nodeCount: project.nodeCount,
          }),
        }));
      } catch (error) {
        if (reqSeq !== openProjectRequestSeq) return;
        console.error('Failed to open project', error);
        set({ isOpeningProject: false });
      }
    })();
  },

  closeProject: () => {
    openProjectRequestSeq += 1;
    useCanvasStore.getState().closeImageViewer();

    const { currentProjectId, currentProject } = get();
    let persistedSummary: ProjectSummary | null = null;

    if (currentProjectId && currentProject && currentProject.id === currentProjectId) {
      const canvasState = useCanvasStore.getState();
      const nextProject: Project = {
        ...currentProject,
        nodes: canvasState.nodes,
        edges: canvasState.edges,
        viewport: canvasState.currentViewport ?? currentProject.viewport ?? DEFAULT_VIEWPORT,
        history: canvasState.history ?? currentProject.history ?? createEmptyHistory(),
        nodeCount: canvasState.nodes.length,
        updatedAt: Date.now(),
      };

      persistedSummary = {
        id: nextProject.id,
        name: nextProject.name,
        createdAt: nextProject.createdAt,
        updatedAt: nextProject.updatedAt,
        nodeCount: nextProject.nodeCount,
      };

      // Flush final sync immediately
      void persistCanvasSync(currentProjectId, canvasState.nodes, canvasState.edges);
    }

    useStoryboardStore.getState().setCurrentProject(null);

    set((state) => ({
      projects: persistedSummary
        ? updateProjectSummary(state.projects, persistedSummary)
        : state.projects,
      currentProjectId: null,
      currentProject: null,
      isOpeningProject: false,
    }));
  },

  getCurrentProject: () => {
    const { currentProjectId, currentProject } = get();
    if (!currentProjectId || !currentProject) return null;
    if (currentProject.id !== currentProjectId) return null;
    return currentProject;
  },

  saveCurrentProject: (nodes, edges, viewport, history) => {
    const { currentProjectId, currentProject } = get();
    if (!currentProjectId || !currentProject || currentProject.id !== currentProjectId) return;

    const nextViewport = viewport ?? currentProject.viewport ?? DEFAULT_VIEWPORT;
    const nextHistory = history ?? currentProject.history ?? createEmptyHistory();
    const nextNodeCount = nodes.length;

    const hasChanged =
      currentProject.nodes !== nodes ||
      currentProject.edges !== edges ||
      currentProject.history !== nextHistory ||
      currentProject.nodeCount !== nextNodeCount;
    if (!hasChanged) return;

    const nextProject: Project = {
      ...currentProject,
      nodes,
      edges,
      viewport: nextViewport,
      history: nextHistory,
      nodeCount: nextNodeCount,
      updatedAt: Date.now(),
      isDirty: true,
    };

    set((state) => ({
      currentProject: nextProject,
      projects: updateProjectSummary(state.projects, {
        id: nextProject.id,
        name: nextProject.name,
        createdAt: nextProject.createdAt,
        updatedAt: nextProject.updatedAt,
        nodeCount: nextProject.nodeCount,
      }),
    }));

    scheduleSyncDebounced(currentProjectId, nodes, edges);
  },

  saveCurrentProjectViewport: (viewport) => {
    const { currentProjectId, currentProject } = get();
    if (!currentProjectId || !currentProject || currentProject.id !== currentProjectId) return;

    const nextViewport = normalizeViewport(viewport);
    const hasChanged = hasViewportMeaningfulDelta(currentProject.viewport, nextViewport);
    if (!hasChanged) return;

    const nextProject: Project = {
      ...currentProject,
      viewport: nextViewport,
    };

    set({ currentProject: nextProject });
    scheduleViewportDebounced(currentProjectId, nextViewport);
  },

  cancelPendingViewportPersist: () => {
    const currentProjectId = get().currentProjectId;
    if (!currentProjectId) return;
    clearViewportTimer(currentProjectId);
  },

  markDirty: () => {
    set((state) => {
      if (!state.currentProject) return state;
      return { currentProject: { ...state.currentProject, isDirty: true } };
    });
  },

  markClean: () => {
    set((state) => {
      if (!state.currentProject) return state;
      return { currentProject: { ...state.currentProject, isDirty: false } };
    });
  },
}));

// ─── Debounced Persist Helpers (module-level) ───────────────────────────────

async function persistCanvasSync(
  projectId: string,
  nodes: CanvasNode[],
  edges: CanvasEdge[]
): Promise<void> {
  if (queue.deletingIds.has(projectId) || queue.syncInFlight.has(projectId)) return;

  queue.syncInFlight.add(projectId);
  try {
    await syncCanvas(projectId, {
      nodes: nodes.map((n) => ({
        id: n.id,
        position_x: n.position.x,
        position_y: n.position.y,
        width: n.measured?.width ?? n.width ?? null,
        height: n.measured?.height ?? n.height ?? null,
        data_json: n.data as Record<string, unknown>,
        sort_order: null,
        locked: (n.data as Record<string, unknown>)?.locked === true,
      })),
      edges: edges.map((e) => ({
        id: e.id,
        source_node_id: e.source,
        target_node_id: e.target,
        source_handle: e.sourceHandle ?? 'source',
        target_handle: e.targetHandle ?? 'target',
        edge_type: 'default',
      })),
    });

    useProjectStore.getState().markClean();
  } catch (error) {
    console.error('Failed to persist canvas sync', error);
  } finally {
    queue.syncInFlight.delete(projectId);
  }
}

function scheduleSyncDebounced(
  projectId: string,
  nodes: CanvasNode[],
  edges: CanvasEdge[]
): void {
  clearSyncTimer(projectId);
  clearViewportTimer(projectId);

  const timer = setTimeout(() => {
    queue.syncTimers.delete(projectId);
    void persistCanvasSync(projectId, nodes, edges);
  }, UPSERT_DEBOUNCE_MS);

  queue.syncTimers.set(projectId, timer);
}

function scheduleViewportDebounced(
  projectId: string,
  viewport: Viewport
): void {
  clearViewportTimer(projectId);

  const timer = setTimeout(() => {
    queue.viewportTimers.delete(projectId);

    if (queue.deletingIds.has(projectId) || queue.viewportInFlight.has(projectId)) return;

    queue.viewportInFlight.add(projectId);
    void apiUpdateViewport(projectId, viewport)
      .catch((error) => console.error('Failed to persist viewport', error))
      .finally(() => queue.viewportInFlight.delete(projectId));
  }, VIEWPORT_UPSERT_DEBOUNCE_MS);

  queue.viewportTimers.set(projectId, timer);
}
