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
 *   - `setViewport`          — programmatic pan/zoom (bumps revision immediately)
 *   - `setViewportOnMove`    — RAF-throttled path for onMove (no revision bump per tick)
 *   - `flushViewportDirty`   — called at RAF frequency to batch viewport dirty signals
 *   - `noteDragStart()`      — captures pre-drag history base without starting timer
 *   - `setNodesDragTick()`   — mid-drag position update (no history timer reset)
 *   - `markDirty()`          — schedules a debounced save
 *   - `flushSave()`          — explicit save (e.g. on blur / route leave)
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

/** React Flow-internal fields that must never be persisted to `nodes_json`.
 *  `selected`/`dragging` are UI state; `measured`/`width`/`height`/`positionAbsolute`
 *  are layout output React Flow recomputes on load. Persisting them bloats the
 *  row and makes two editors' rows diverge → false realtime conflicts. */
const RF_INTERNAL_KEYS = [
  'selected',
  'dragging',
  'measured',
  'width',
  'height',
  'positionAbsolute',
] as const;

function stripRfInternals(node: CanvasNode): CanvasNode {
  const obj = node as Record<string, unknown>;
  let dirty = false;
  for (const k of RF_INTERNAL_KEYS) {
    if (k in obj) {
      dirty = true;
      break;
    }
  }
  if (!dirty) return node;
  const clone: Record<string, unknown> = { ...obj };
  for (const k of RF_INTERNAL_KEYS) delete clone[k];
  return clone as CanvasNode;
}

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
  /** Patch a single node's `data` (or top-level fields) in place. Used
   *  by the runner to bump run_status without rebuilding the full
   *  nodes array. Marks dirty but does NOT push to history (run
   *  lifecycle is operational state, not a user-undoable edit). */
  patchNode(
    id: string,
    patch: { data?: Record<string, unknown>; [k: string]: unknown },
  ): void;

  // ---- Phase 6e performance: drag-tick + viewport throttle ----
  /**
   * Capture the pre-drag history snapshot without starting the 250ms
   * history-debounce timer. Call this from React Flow's `onNodeDragStart`.
   *
   * Behaviour: identical to the guard in `noteDocumentEditStarting` —
   * if a `pendingHistoryBase` is already set (consecutive drags within
   * the same window) it is kept intact so undo lands on the state before
   * the FIRST drag.  The history timer is started only by the drag-end
   * `setNodes` call, which reduces timer-reset churn from O(drag_ticks)
   * to O(1) per drag gesture.
   */
  noteDragStart(): void;

  /**
   * Update node positions during a mid-drag tick.  Stores the new nodes
   * array and marks the document dirty for eventual persistence, but does
   * NOT touch the history-debounce timer.  The timer is started by the
   * drag-end `setNodes` call so each drag produces exactly one history
   * entry regardless of how many ticks it spans.
   */
  setNodesDragTick(nodes: CanvasNode[]): void;

  /**
   * Replace the nodes array for RENDER purposes only — no history entry, no
   * revision bump, no save. Used for React Flow-internal change types
   * (`select`, `dimensions`/measurement) that must never enter the undo
   * history or be persisted to `nodes_json`. Selection is tracked separately
   * (see `setSelection`); measurement is RF-internal and stripped on save.
   */
  setNodesTransient(nodes: CanvasNode[]): void;

  /**
   * Commit any in-flight edit burst's history base immediately. Call on
   * surface unmount so a drag/edit that never reached its debounce commit
   * does not leak its pending base into the next mount of the singleton store.
   */
  flushHistory(): void;

  /**
   * Update the viewport during an `onMove` tick without bumping `revision`
   * or scheduling a save.  Callers must pair this with `flushViewportDirty`
   * (called at RAF frequency) to coalesce N per-tick revision bumps into
   * at most one per animation frame.
   *
   * Programmatic viewport changes (panViewportBy, zoomViewportAround, etc.)
   * continue to use `setViewport` which bumps revision immediately.
   */
  setViewportOnMove(viewport: CanvasViewport): void;

  /**
   * Bump `revision` and schedule a debounced save.  Intended to be called
   * at RAF frequency from `CanvasSurface.onMove` rather than on every
   * wheel/pan tick, reducing save-debounce timer-reset churn from
   * O(pan_ticks) to O(1) per animation frame.
   */
  flushViewportDirty(): void;

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

  // ---- Realtime sync (Phase 6a) ----
  /**
   * Called by useCanvasRealtime when Supabase Realtime broadcasts an UPDATE
   * on the canvases row. Three cases:
   *   - Self-echo / stale: row.base_updated_at <= current → no-op
   *   - Newer + no unsaved edits → rebase to remote row
   *   - Newer + unsaved local edits → surface as conflict (reuse 409 path)
   */
  applyRemoteUpdate(row: Canvas): void;
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
          // Strip React Flow-internal fields — `selected`/`dragging` are UI
          // state and `measured`/`width`/`height` are layout output; none
          // belong in the persisted document (they also spuriously diverge
          // two editors' rows and trigger false realtime conflicts).
          nodes_json: state.nodes.map(stripRfInternals),
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

      patchNode(id, patch) {
        const { nodes } = get();
        let changed = false;
        const next = nodes.map((node) => {
          const obj = node as Record<string, unknown>;
          if (obj.id !== id) return node;
          changed = true;
          const mergedData =
            patch.data && typeof obj.data === 'object' && obj.data !== null
              ? { ...(obj.data as Record<string, unknown>), ...patch.data }
              : (patch.data ?? obj.data);
          const { data: _ignored, ...topLevel } = patch;
          return {
            ...obj,
            ...topLevel,
            ...(mergedData !== undefined ? { data: mergedData } : {}),
          } as CanvasNode;
        });
        if (!changed) return;
        // Do NOT call noteDocumentEditStarting — runtime status churn
        // shouldn't pollute the undo stack.
        set({ nodes: next });
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
        // Symmetric with undo(): flush any in-flight edit burst first. This
        // commits the pending base (which clears historyFuture), so a redo
        // pressed inside the 250ms debounce window after a NEW edit correctly
        // no-ops instead of resurrecting a future that edit already invalidated.
        flushPendingHistory();
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

      // ---- Phase 6e performance: drag-tick + viewport throttle ----

      noteDragStart() {
        // Capture pre-drag snapshot only once per burst — the guard ensures
        // consecutive drags within the same debounce window share one entry.
        if (pendingHistoryBase === null) {
          const { nodes, connections } = get();
          pendingHistoryBase = { nodes, connections };
        }
        // Intentionally NO historyTimer start here.  The drag-end setNodes
        // call uses noteDocumentEditStarting which starts the timer exactly
        // once, reducing timer-reset churn from O(drag_ticks) to O(1).
      },

      setNodesDragTick(nodes: CanvasNode[]) {
        // Mid-drag: update positions, schedule save — but do NOT touch the
        // history timer.  The pre-drag base was captured by noteDragStart();
        // the drag-end setNodes() call will start the 250ms commit timer.
        set({ nodes });
        markDirty();
      },

      setNodesTransient(nodes: CanvasNode[]) {
        // Render-only update (select / measurement). No history, no dirty,
        // no save — these change types must never become undoable edits or
        // reach persistence.
        set({ nodes });
      },

      flushHistory() {
        flushPendingHistory();
      },

      setViewportOnMove(viewport: CanvasViewport) {
        // Update viewport for controlled-mode React Flow rendering without
        // bumping revision.  Callers (CanvasSurface.onMove via RAF) call
        // flushViewportDirty() at most once per animation frame.
        set({ viewport: { ...viewport, zoom: clampZoom(viewport.zoom) } });
      },

      flushViewportDirty() {
        // Called at RAF frequency — bumps revision and schedules a save.
        markDirty();
      },

      // ---- Realtime sync (Phase 6a) ----
      applyRemoteUpdate(row: Canvas) {
        const s = get();

        // Guard 1: store not loaded yet — ignore.
        if (!s.baseUpdatedAt) return;

        // Guard 2: self-echo / stale broadcast — ignore.
        if (row.base_updated_at <= s.baseUpdatedAt) return;

        // Guard 3: local dirty edits exist — surface as conflict, never clobber.
        if (s.revision > s.persistedRevision) {
          set({ conflict: row });
          return;
        }

        // Happy path: newer row, clean local state — rebase.
        applyServerRow(row);
      },
    };
  });

  return useStore;
}

/** Default singleton (one open canvas at a time, current product shape). */
export const useCanvasCoreStore = createCanvasCoreStore();
