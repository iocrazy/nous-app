/**
 * Canvas-core Zustand store (Phase 1 Day 2-7).
 *
 * Holds the in-memory state of a single open canvas, plus a debounced
 * persist-to-server side effect. The store is the single source of
 * truth that the React Flow surface renders from.
 *
 * Coordinates with the backend save endpoint via:
 *   - `loadCanvas(id)`  — pull the row
 *   - `applyNodeChanges(...)` / `applyConnectionChanges(...)` (TBD in a
 *     follow-up React Flow integration PR)
 *   - `setViewport`     — wheel/pinch/pan
 *   - `markDirty()`     — schedules a debounced save
 *   - `flushSave()`     — explicit save (e.g. on blur / route leave)
 *
 * On a 409 from the server we drop the in-flight save and surface the
 * server's snapshot as `conflict` so the UI can render a merge prompt.
 * The store does NOT auto-resolve conflicts — that's a UX decision the
 * Canvas page owns.
 */

import { create } from 'zustand';

import {
  getCanvas as fetchCanvas,
  saveCanvas,
} from '../services/canvasService';
import type {
  Canvas,
  CanvasConnection,
  CanvasKind,
  CanvasNode,
  CanvasViewport,
} from '../types';
import {
  IDENTITY_VIEWPORT,
  clampZoom,
  panByScreenDelta,
  zoomAroundScreenAnchor,
} from '../utils/viewport';

const DEFAULT_DEBOUNCE_MS = 500;
const DEFAULT_HISTORY_DEBOUNCE_MS = 250;
const MAX_HISTORY = 30;

export type CanvasLoadStatus = 'idle' | 'loading' | 'ready' | 'error';
export type CanvasSaveStatus = 'idle' | 'saving' | 'error';

/** Document-state snapshot pushed onto the undo stack. Selection is
 *  NOT part of history (UI state), and viewport is NOT either (panning
 *  / zooming is too noisy to keep on undo and users don't expect it). */
export interface HistorySnapshot {
  nodes: CanvasNode[];
  connections: CanvasConnection[];
}

interface CanvasState {
  // ---- Lifecycle ----
  canvasId: string | null;
  /** smart = Infinite-Canvas-style with shot/prompt/output nodes;
   *  classic = legacy block editor. Surface uses this to pick the
   *  React Flow nodeTypes map and toolbar. NULL until loadCanvas
   *  resolves. */
  kind: CanvasKind | null;
  loadStatus: CanvasLoadStatus;
  loadError: string | null;

  // ---- Document ----
  viewport: CanvasViewport;
  nodes: CanvasNode[];
  connections: CanvasConnection[];
  nodeOps: Record<string, unknown>[];
  connectionOps: Record<string, unknown>[];

  // ---- Lock + persistence ----
  baseUpdatedAt: string | null;
  saveStatus: CanvasSaveStatus;
  saveError: string | null;
  conflict: Canvas | null;
  /** Monotonic counter — bumped by every mutation, used by the save tick
   *  to know whether the snapshot it grabbed is still the latest. */
  revision: number;
  /** Revision the most recent persisted save reflected. */
  persistedRevision: number;

  // ---- Selection (UI state, not persisted, not in history) ----
  selection: string[];

  // ---- Undo / Redo (document-state only) ----
  historyPast: HistorySnapshot[];
  historyFuture: HistorySnapshot[];

  // ---- Reset / load ----
  reset(): void;
  loadCanvas(canvasId: string): Promise<void>;

  // ---- Mutations (mark dirty) ----
  setViewport(viewport: CanvasViewport): void;
  panViewportBy(dx: number, dy: number): void;
  zoomViewportAround(anchor: { x: number; y: number }, nextZoom: number): void;
  setNodes(nodes: CanvasNode[]): void;
  setConnections(connections: CanvasConnection[]): void;

  // ---- Selection ----
  setSelection(ids: string[]): void;
  selectAll(): void;
  clearSelection(): void;

  // ---- History ----
  undo(): void;
  redo(): void;
  canUndo(): boolean;
  canRedo(): boolean;

  // ---- Persist ----
  flushSave(): Promise<void>;
  resolveConflictWithServer(): void;
  dismissConflict(): void;
}

interface CanvasStoreFactoryOptions {
  debounceMs?: number;
  historyDebounceMs?: number;
  saveImpl?: typeof saveCanvas;
  loadImpl?: typeof fetchCanvas;
}

export function createCanvasCoreStore(
  options: CanvasStoreFactoryOptions = {},
) {
  const debounceMs = options.debounceMs ?? DEFAULT_DEBOUNCE_MS;
  const historyDebounceMs =
    options.historyDebounceMs ?? DEFAULT_HISTORY_DEBOUNCE_MS;
  const saveImpl = options.saveImpl ?? saveCanvas;
  const loadImpl = options.loadImpl ?? fetchCanvas;
  // Per-factory-call timers. Naturally scoped to this store —
  // multiple createCanvasCoreStore() calls each get their own,
  // including the per-test instances in vitest.
  let debounceTimer: ReturnType<typeof setTimeout> | null = null;
  let historyTimer: ReturnType<typeof setTimeout> | null = null;
  /** Snapshot taken at the START of an edit burst — pushed to historyPast
   *  when the debounced commit fires. Lets a user undo back to the state
   *  BEFORE the edit, not to a mid-burst intermediate. */
  let pendingHistoryBase: HistorySnapshot | null = null;

  const useStore = create<CanvasState>((set, get) => {
    function applyServerRow(row: Canvas): void {
      set({
        canvasId: row.id,
        kind: row.kind,
        viewport: row.viewport_json ?? IDENTITY_VIEWPORT,
        nodes: row.nodes_json ?? [],
        connections: row.connections_json ?? [],
        nodeOps: row.node_ops_json ?? [],
        connectionOps: row.connection_ops_json ?? [],
        baseUpdatedAt: row.base_updated_at,
        loadStatus: 'ready',
        loadError: null,
        saveStatus: 'idle',
        saveError: null,
        conflict: null,
        revision: 0,
        persistedRevision: 0,
        selection: [],
        historyPast: [],
        historyFuture: [],
      });
      pendingHistoryBase = null;
    }

    function markDirty(): void {
      const next = get().revision + 1;
      set({ revision: next, saveStatus: 'idle', saveError: null });
      scheduleSave();
    }

    /**
     * Called by setNodes / setConnections BEFORE the new state is applied
     * — captures the pre-edit snapshot exactly once per debounce window so
     * undo lands on the state before the edit burst started.
     */
    function noteDocumentEditStarting(): void {
      if (pendingHistoryBase === null) {
        const { nodes, connections } = get();
        pendingHistoryBase = { nodes, connections };
      }
      if (historyTimer) clearTimeout(historyTimer);
      historyTimer = setTimeout(() => {
        historyTimer = null;
        commitPendingHistory();
      }, historyDebounceMs);
    }

    function commitPendingHistory(): void {
      const base = pendingHistoryBase;
      pendingHistoryBase = null;
      if (!base) return;
      const { historyPast } = get();
      const nextPast = [...historyPast, base];
      // Ring-cap from the FRONT — drop the oldest entries.
      while (nextPast.length > MAX_HISTORY) nextPast.shift();
      // Any forward redos are invalidated by a new edit.
      set({ historyPast: nextPast, historyFuture: [] });
    }

    function flushPendingHistory(): void {
      if (historyTimer) {
        clearTimeout(historyTimer);
        historyTimer = null;
      }
      commitPendingHistory();
    }

    function scheduleSave(): void {
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        debounceTimer = null;
        void doSave();
      }, debounceMs);
    }

    async function doSave(): Promise<void> {
      const state = get();
      if (!state.canvasId || !state.baseUpdatedAt) return;
      if (state.persistedRevision >= state.revision) return; // nothing new
      if (state.conflict) return; // user must resolve first

      const snapshot = {
        revision: state.revision,
        baseUpdatedAt: state.baseUpdatedAt,
        payload: {
          base_updated_at: state.baseUpdatedAt,
          viewport_json: state.viewport,
          nodes_json: state.nodes,
          connections_json: state.connections,
          node_ops_json: state.nodeOps,
          connection_ops_json: state.connectionOps,
        },
      };

      set({ saveStatus: 'saving', saveError: null });

      let result;
      try {
        result = await saveImpl(state.canvasId, snapshot.payload);
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        set({ saveStatus: 'error', saveError: message });
        return;
      }

      if (result.ok) {
        // Only accept if no further edits raced this save — if more
        // mutations happened, leave revision diff alone, the next save
        // tick will pick them up.
        const latest = get();
        const accepted = latest.revision === snapshot.revision;
        set({
          baseUpdatedAt: result.canvas.base_updated_at,
          saveStatus: 'idle',
          saveError: null,
          persistedRevision: snapshot.revision,
        });
        if (!accepted) scheduleSave();
      } else {
        // 409: surface the server row, freeze auto-save until the user
        // resolves.
        set({
          saveStatus: 'error',
          saveError: 'conflict',
          conflict: result.conflict,
        });
      }
    }

    return {
      canvasId: null,
      kind: null,
      loadStatus: 'idle',
      loadError: null,
      viewport: IDENTITY_VIEWPORT,
      nodes: [],
      connections: [],
      nodeOps: [],
      connectionOps: [],
      baseUpdatedAt: null,
      saveStatus: 'idle',
      saveError: null,
      conflict: null,
      revision: 0,
      persistedRevision: 0,
      selection: [],
      historyPast: [],
      historyFuture: [],

      reset() {
        if (debounceTimer) clearTimeout(debounceTimer);
        debounceTimer = null;
        if (historyTimer) clearTimeout(historyTimer);
        historyTimer = null;
        pendingHistoryBase = null;
        set({
          canvasId: null,
          kind: null,
          loadStatus: 'idle',
          loadError: null,
          viewport: IDENTITY_VIEWPORT,
          nodes: [],
          connections: [],
          nodeOps: [],
          connectionOps: [],
          baseUpdatedAt: null,
          saveStatus: 'idle',
          saveError: null,
          conflict: null,
          revision: 0,
          persistedRevision: 0,
          selection: [],
          historyPast: [],
          historyFuture: [],
        });
      },

      async loadCanvas(canvasId: string) {
        set({ loadStatus: 'loading', loadError: null });
        try {
          const row = await loadImpl(canvasId);
          applyServerRow(row);
        } catch (err) {
          const message = err instanceof Error ? err.message : String(err);
          set({ loadStatus: 'error', loadError: message });
        }
      },

      setViewport(viewport: CanvasViewport) {
        set({ viewport: { ...viewport, zoom: clampZoom(viewport.zoom) } });
        markDirty();
      },

      panViewportBy(dx, dy) {
        set({ viewport: panByScreenDelta(get().viewport, dx, dy) });
        markDirty();
      },

      zoomViewportAround(anchor, nextZoom) {
        const clamped = clampZoom(nextZoom);
        set({
          viewport: zoomAroundScreenAnchor(get().viewport, anchor, clamped),
        });
        markDirty();
      },

      setNodes(nodes) {
        noteDocumentEditStarting();
        set({ nodes });
        markDirty();
      },

      setConnections(connections) {
        noteDocumentEditStarting();
        set({ connections });
        markDirty();
      },

      setSelection(ids) {
        // dedupe + stable order so equality checks are predictable
        const unique = Array.from(new Set(ids));
        set({ selection: unique });
      },

      selectAll() {
        const allIds = get()
          .nodes.map((n) => {
            const obj = n as Record<string, unknown>;
            return typeof obj.id === 'string' ? obj.id : null;
          })
          .filter((v): v is string => v !== null);
        set({ selection: allIds });
      },

      clearSelection() {
        set({ selection: [] });
      },

      undo() {
        // Flush any in-flight edit burst so its base is on the stack
        // before we pop — otherwise undo would skip the most recent edit.
        flushPendingHistory();
        const { historyPast, nodes, connections } = get();
        if (historyPast.length === 0) return;
        const nextPast = historyPast.slice(0, -1);
        const popped = historyPast[historyPast.length - 1];
        const presentSnapshot: HistorySnapshot = { nodes, connections };
        set({
          historyPast: nextPast,
          historyFuture: [presentSnapshot, ...get().historyFuture],
          nodes: popped.nodes,
          connections: popped.connections,
        });
        markDirty();
      },

      redo() {
        const { historyFuture } = get();
        if (historyFuture.length === 0) return;
        const [next, ...rest] = historyFuture;
        const presentSnapshot: HistorySnapshot = {
          nodes: get().nodes,
          connections: get().connections,
        };
        set({
          historyFuture: rest,
          historyPast: [...get().historyPast, presentSnapshot],
          nodes: next.nodes,
          connections: next.connections,
        });
        markDirty();
      },

      canUndo() {
        return get().historyPast.length > 0 || pendingHistoryBase !== null;
      },

      canRedo() {
        return get().historyFuture.length > 0;
      },

      async flushSave() {
        if (debounceTimer) {
          clearTimeout(debounceTimer);
          debounceTimer = null;
        }
        await doSave();
      },

      resolveConflictWithServer() {
        const { conflict } = get();
        if (conflict) applyServerRow(conflict);
      },

      dismissConflict() {
        set({ conflict: null, saveStatus: 'idle', saveError: null });
      },
    };
  });

  return useStore;
}

/** Default singleton (one open canvas at a time, current product shape). */
export const useCanvasCoreStore = createCanvasCoreStore();
