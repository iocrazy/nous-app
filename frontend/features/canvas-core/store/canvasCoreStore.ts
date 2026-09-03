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
 *   - `setViewportSettled`   — the pan/zoom gesture ended here; write + dirty
 *                              once, and NO epoch bump (it is already on
 *                              screen). The ONLY viewport writer a gesture
 *                              uses; there are no programmatic ones left —
 *                              a caller that wants to MOVE the canvas drives
 *                              React Flow's instance directly (`zoomPreview`,
 *                              `CanvasPage`'s overview fly-in)
 *   - `noteDragStart()`      — captures pre-drag history base without starting timer
 *   - `setNodesDragTick()`   — mid-drag position update (no history timer,
 *                              no dirty — drag end owns both)
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
import { healStaleGenSlots } from '../smart/healGenSlots';
import { CONFLICT_SAVE_ERROR, readErrorStatus } from '../utils/saveFailure';
import { IDENTITY_VIEWPORT, clampZoom } from '../utils/viewport';

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

/**
 * Drop every node after the FIRST occurrence of its id (2026-08-12
 * production incident). React Flow builds its internal `nodeLookup` keyed by
 * id — duplicated ids leave the whole node layer permanently
 * `visibility:hidden` (the measurement echo lands on one copy while the
 * lookup keeps another, so nodes never "initialize"): a poisoned row renders
 * as a BLANK canvas with only the zoom controls. Applied at load
 * (`applyServerRow`) so any row poisoned by the numeric-shot-id reconcile
 * regression — or any future duplicate producer — renders correctly the
 * moment it's opened; the healed set then reaches the DB through whichever
 * normal save fires next. Nodes with a missing/non-string id are kept as-is
 * (`toReactFlowNodes` assigns those a positional fallback id).
 */
export function dedupeNodesById(nodes: CanvasNode[]): CanvasNode[] {
  const seen = new Set<string>();
  let dropped = false;
  const out: CanvasNode[] = [];
  for (const node of nodes) {
    const id = (node as Record<string, unknown>).id;
    if (typeof id === 'string') {
      if (seen.has(id)) {
        dropped = true;
        continue;
      }
      seen.add(id);
    }
    out.push(node);
  }
  if (dropped) {
    console.warn(
      `[canvasCoreStore] dropped ${nodes.length - out.length} duplicate-id node(s) — duplicated ids blank the React Flow surface`,
    );
  }
  return dropped ? out : nodes;
}

export function stripRfInternals(node: CanvasNode): CanvasNode {
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

/**
 * A node reconcile flagged as stale (Task 4 shotSync — its bound `shot_id`
 * no longer resolves to a `script_shots` row). Generic on purpose: the store
 * doesn't know about `ShotNodeData`, it just excludes any node whose `data`
 * carries this one marker key from what `doSave` persists — "在下一次用户
 * 保存时由编排层过滤删除" from the brief, read literally as filtering the
 * PAYLOAD (review fix round 1: an earlier version also cleared these nodes
 * out of local `state.nodes` inside `doSave`, which bypassed undo history
 * and made an unrelated edit's autosave tick silently delete a stale card's
 * local render — see `doSave`'s comment). Harmless for every other node
 * type since nothing else ever sets this key.
 */
function isStaleNode(node: CanvasNode): boolean {
  const data = (node as Record<string, unknown>).data;
  return !!data && typeof data === 'object' && (data as Record<string, unknown>).stale === true;
}

export type CanvasLoadStatus = 'idle' | 'loading' | 'ready' | 'error';
export type CanvasSaveStatus = 'idle' | 'saving' | 'error';

/**
 * Does this error mean "you may read this canvas but not write it"?
 *
 * Duck-typed on `status` rather than `instanceof ApiError`: `saveImpl` is an
 * injectable dependency (tests, and any future transport), so the store must
 * not require one specific Error subclass to recognise the wire condition it
 * cares about. `canvasService.saveCanvas` throws `ApiError(message, 403)` for
 * a viewer-role PUT, which satisfies this.
 */
function isForbidden(err: unknown): boolean {
  if (!err || typeof err !== 'object') return false;
  return (err as { status?: unknown }).status === 403;
}

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
  /** smart-family = Infinite-Canvas-style with shot/prompt/output
   *  nodes. Surface uses this to pick the React Flow nodeTypes map
   *  and toolbar. NULL until loadCanvas resolves. */
  kind: CanvasKind | null;
  /** Display name from the server row — read-only chrome (back-pill area). */
  name: string | null;
  /** Owning project (row's `project_id`) — read-only, NULL until load
   *  resolves. Task 4 orchestration (shot-node reconcile) resolves the
   *  storyboard canvas's script through this. */
  projectId: string | null;
  /** Owning episode for a `kind==='storyboard'` row (Task 1); NULL for
   *  every other kind, and NULL until load resolves. Gates Task 4's
   *  reconcile orchestration in `CanvasPage.tsx`. */
  episodeId: string | null;
  /**
   * The asset this canvas belongs to (`canvases.asset_id`, mig 446), or NULL.
   *
   * Set by "Open In Canvas" on the asset sheet, which is what makes an empty
   * entity canvas seedable: P4 Task 6 gives such a canvas ONE asset card bound
   * to this id, replacing the old `?characterId=` query-parameter seeding whose
   * producer was retired in P3. NULL until load resolves, and NULL for every
   * canvas that was not created from an asset.
   */
  assetId: string | null;
  loadStatus: CanvasLoadStatus;
  loadError: string | null;
  /**
   * Monotonic mount-generation counter, bumped by every `loadCanvas()` call
   * (Task 5 评审修复轮1 — generation guard against a stale-unmount race).
   * Embedding this store's view in a frequently-toggled tab (the storyboard
   * page's Canvas tab) turned what used to be a rare route-navigation
   * unmount into a high-frequency event: a fast tab-away-then-back can
   * mount a NEW instance (bumping this) before the OLD instance's own
   * unmount `flushSave().finally(reset)` tail resolves — without this
   * guard, that stale `reset()` slams the NEW instance's already-`ready`
   * state back to `idle` with nobody left to re-trigger `loadCanvas`,
   * permanently stuck on "Loading canvas…". See `CanvasPage.tsx`'s unmount
   * cleanup for the read side of this guard.
   */
  mountEpoch: number;

  /**
   * Bumped by every viewport the STORE writes itself — the load, a canvas
   * switch, a realtime rebase, a conflict resolve, `reset()`. NOT bumped by
   * `setViewportSettled`, which carries a viewport that came FROM the canvas
   * and is therefore already on screen.
   *
   * The invariant is "every store-side `viewport:` write bumps the epoch in
   * the SAME set literal", and it is enforced by a source scan in
   * `canvasCoreStore.viewport.test.ts` — a convention with no enforcement
   * erodes the moment someone adds a writer.
   *
   * The reason this exists (Task 3 评审修复轮1): React Flow runs uncontrolled,
   * so a store write no longer moves anything — only an imperative
   * `setViewport` on React Flow's own instance does, and the store cannot
   * reach that instance. This counter is how a store-side write says "the
   * canvas needs moving"; `CanvasPage` owns the single effect that answers it.
   * Without it the store and the visible transform silently diverge, and the
   * two placement readers (`TopNodeBar`, `CanvasComposer`) map screen
   * coordinates through a viewport nobody is looking at.
   */
  viewportEpoch: number;

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
  /**
   * HTTP status of the failure `saveError` describes, or `null` when the
   * request produced no response at all (offline / DNS / TLS / timeout).
   *
   * The thrown value only exists inside `doSave`'s catch, so the status is
   * extracted there and carried alongside the message; `classifySaveFailure`
   * (utils/saveFailure.ts) turns the pair into the badge's category. Written
   * ONLY by the two branches that set `saveStatus: 'error'`, which is what
   * keeps it from going stale: a status can never outlive the message it
   * belongs to.
   */
  saveErrorStatus: number | null;
  /**
   * This session may not write the canvas.
   *
   * Set UP FRONT from the load response's `can_edit` (the same verdict the
   * PUT's write guard reaches — `scope_guards.resolve_project_read_access`), so a
   * viewer never sends the doomed PUT that used to be the only way to find
   * out. `doSave`'s 403 latch is kept as the BACKSTOP for what the load
   * can't know: an older backend that doesn't send the field, and a
   * permission revoked mid-session.
   *
   * Three consequences, all about not turning a permission fact into a
   * failure loop (2026-08-12 production: a viewer's load-time
   * sanitize/reconcile autosave 403'd, the debounce re-armed on every
   * subsequent change and the UI flashed "Save failed" — two PUTs inside the
   * same second observed on the real row):
   *   1. `markDirty` stops scheduling saves, and `doSave` early-returns, so
   *      NO PUT is ever sent from a read-only load.
   *   2. The badge reads "Read-only" instead of the red "Save failed".
   *   3. The surface withdraws its editing gestures — see `CanvasSurface`
   *      (drag / connect / knife / create menu), `useCanvasShortcuts`
   *      (delete / paste / duplicate / group / undo) and the command
   *      palette. Panning, zooming and selecting-to-inspect stay live:
   *      they are how a viewer reads the document, and none of them dirty it.
   */
  readOnly: boolean;
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

  /** Append runtime-produced nodes/edges (loop output slots) in one atomic
   *  set. Marks dirty but does NOT push to history — operational output,
   *  not a user-undoable edit (mirrors patchNode's contract). Nodes whose id
   *  already exists in the store (or earlier in the same batch) are DROPPED:
   *  duplicated ids blank the whole React Flow surface (2026-08-12
   *  incident), so the write point itself refuses to create them no matter
   *  what the caller's diff logic concluded. */
  appendElementsNoHistory(nodes: CanvasNode[], connections: CanvasConnection[]): void;

  /** Collapse the listed node ids down to their FIRST occurrence each
   *  (2026-08-12 self-heal — `ShotSyncResult.nodeIdsToDedupe`). No history
   *  entry (an automated repair must not become an undo step); marks dirty
   *  so the healed set persists through the normal debounced save. */
  dedupeNodes(ids: string[]): void;

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
   * Update node positions during a mid-drag tick. Stores the new nodes
   * array and NOTHING else: no history-debounce timer, and (canvas fluency
   * Task 6) no dirty/revision bump either. The drag-end `setNodes` call
   * does both, so each drag produces exactly one history entry and one
   * armed save regardless of how many ticks it spans.
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
   * A pan/zoom gesture settled at this viewport — write it and mark dirty,
   * exactly once. This is the ONLY viewport channel the canvas surface has:
   * React Flow owns the transform mid-gesture (uncontrolled, seeded from
   * `defaultViewport`), so the frames in between never reach the store.
   *
   * Replaces the `setViewportOnMove` + RAF `flushViewportDirty` pair, which
   * existed only to make a per-frame controlled-mode React state write
   * survivable. Nothing writes per frame any more, so nothing needs
   * coalescing.
   */
  setViewportSettled(viewport: CanvasViewport): void;

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
  /** True while `setNodesDragTick` has written positions that no `markDirty`
   *  has claimed yet (canvas fluency Task 6 — ticks no longer mark dirty).
   *  A drag that ends normally clears this via the drag-end `setNodes`.
   *
   *  INVARIANT: while this is true the document holds unsaved local work that
   *  `revision` does not account for, so EVERY reader of "is the document
   *  dirty?" must consult it too. Two consumers today:
   *    - `flushSave()` — a drag still HELD when the surface unmounts never
   *      gets its drag-end `setNodes`, and `doSave`'s
   *      `persistedRevision >= revision` guard would drop the move.
   *    - `applyRemoteUpdate()` guard 3 — a realtime row arriving mid-drag
   *      must raise a conflict, not rebase the canvas under the pointer.
   *  A third reader added later belongs on this list. */
  let unclaimedDragTick = false;
  /** Backing counter for `mountEpoch` (Task 5 评审修复轮1) — a plain closure
   *  variable rather than reading-then-incrementing store state, so every
   *  `loadCanvas()` call gets a strictly unique value even if called
   *  reentrantly before a previous `set()` has been observed. */
  let mountEpochCounter = 0;
  /** Backing counter for `viewportEpoch` — same reasoning as `mountEpoch`'s:
   *  a closure variable, so two writes in one tick get distinct values even
   *  before either `set()` has been observed. */
  let viewportEpochCounter = 0;

  const useStore = create<CanvasState>((set, get) => {
    /**
     * Adopt a server row as the document.
     *
     * `fromLoad` marks the two calls that are a NEW permission question —
     * `loadCanvas` (a canvas the user just opened). Everything else
     * (`applyRemoteUpdate`'s rebase, `resolveConflictWithServer`) is the
     * SAME canvas in the same session, so it must not silently re-open a
     * write channel the server already refused.
     *
     * Precedence, in order:
     *   1. `row.can_edit` present → it decides. This is the upfront path:
     *      the load response says so before a single PUT is attempted.
     *   2. absent + `fromLoad` → writable (the pre-`can_edit` default; the
     *      403 latch in `doSave` remains the backstop, e.g. against an
     *      older backend or a mid-session permission change).
     *   3. absent + not a load → keep the current latch. A realtime row
     *      carries no permission statement, and treating "silent" as
     *      "writable" would unlock a viewer's surface on every broadcast.
     */
    function applyServerRow(row: Canvas, opts: { fromLoad?: boolean } = {}): void {
      const stated = typeof row.can_edit === 'boolean' ? !row.can_edit : undefined;
      const readOnly = stated ?? (opts.fromLoad ? false : get().readOnly);
      set({
        canvasId: row.id,
        kind: row.kind,
        name: row.name ?? null,
        projectId: row.project_id ?? null,
        episodeId: row.episode_id ?? null,
        assetId: row.asset_id ?? null,
        viewport: row.viewport_json ?? IDENTITY_VIEWPORT,
        // A server row's viewport is a STORE-side write, on all three paths
        // that reach here (`loadCanvas`, `applyRemoteUpdate`'s rebase,
        // `resolveConflictWithServer`). Announce it so `CanvasPage` moves the
        // real transform to match — see `viewportEpoch`.
        viewportEpoch: (viewportEpochCounter += 1),
        // Sanitize on load: interaction paths (alignment snap, group
        // membership) historically persisted RF-internal size snapshots
        // (measured/width/height) into rows — stale ones clamp a node's
        // rendered box below its content (dangling-selects screenshot).
        // `dedupeNodesById`: a row with duplicated node ids renders as a
        // blank canvas (see that helper's doc comment) — collapse to the
        // first occurrence before anything downstream sees the set.
        // `healStaleGenSlots`: a pending shimmer count whose owner (runner /
        // resume registry) is gone would pulse forever — clamp it here, same
        // hook and same spirit as the dedupe above (2026-09-02 screenshot).
        nodes: healStaleGenSlots(
          dedupeNodesById((row.nodes_json ?? []).map(stripRfInternals)),
        ),
        connections: row.connections_json ?? [],
        nodeOps: row.node_ops_json ?? [],
        connectionOps: row.connection_ops_json ?? [],
        baseUpdatedAt: row.base_updated_at,
        loadStatus: 'ready',
        loadError: null,
        saveStatus: 'idle',
        saveError: null,
        saveErrorStatus: null,
        // A new load is a new permission question. The store is a module-level
        // singleton reused across mounts, so this must be ASSIGNED on every
        // load, never merely left alone: a latch left over from a canvas the
        // user could only read would silently mute saves on the NEXT canvas
        // they open with full rights, and (since `can_edit` landed) the
        // reverse leak matters just as much — an unconditional `false` here
        // would hand a viewer a writable surface for the 500ms until the
        // doomed PUT came back 403.
        readOnly,
        conflict: null,
        revision: 0,
        persistedRevision: 0,
        selection: [],
        historyPast: [],
        historyFuture: [],
      });
      pendingHistoryBase = null;
      unclaimedDragTick = false;
    }

    function markDirty(): void {
      // Whatever the outcome below, any drag positions sitting in `nodes`
      // are now accounted for — either by the bump this call makes, or by a
      // read-only session that will never save anything at all.
      unclaimedDragTick = false;
      // Read-only session: no revision bump, no debounce re-arm. Bailing out
      // BEFORE the bump also keeps `revision === persistedRevision`, so an
      // incoming realtime row rebases cleanly instead of raising a conflict
      // dialog a viewer has no way to resolve.
      if (get().readOnly) return;
      const next = get().revision + 1;
      set({ revision: next, saveStatus: 'idle', saveError: null, saveErrorStatus: null });
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
      // Hard stop for a session the server already refused. `markDirty` no
      // longer schedules, but `flushSave()` is also called directly (surface
      // unmount / route leave) — this guard is what makes "no further PUT"
      // true for EVERY path, not just the debounced one.
      if (state.readOnly) return;
      if (state.persistedRevision >= state.revision) return; // nothing new
      if (state.conflict) return; // user must resolve first

      // Drop stale shot nodes (Task 4 shotSync) from the PERSISTED payload
      // only — brief literal: "在下一次用户保存时由编排层过滤删除" is about
      // what gets written, not an instant local-state edit. A review fix
      // (round 1) reverted an earlier version of this that also cleared
      // `state.nodes` here: that bypassed undo history (patchNode/setNodes
      // both go through the store's own history discipline; this
      // filter-inside-doSave path did not) and made an unrelated edit
      // elsewhere on the canvas silently delete a stale node's local render
      // the moment the 500ms autosave tick fired — surprising side effect
      // for an action the user didn't take. The grey stale card now simply
      // stays on screen (still filtered out of every future save payload,
      // so it never round-trips back in) until the canvas is reloaded —
      // storyboard canvases have no composer/connection surface (Task 4
      // deliberately didn't add `'storyboard'` to `isSmartFamily`), so
      // there's no dangling-edge cleanup concern from leaving a stale node
      // visually present a little longer.
      const liveNodes = state.nodes.filter((n) => !isStaleNode(n));

      // Mount-generation guard (Task 5 评审修复轮2 — doSave's own post-await
      // `set()` calls below had no epoch check of their own, a gap flagged
      // but deliberately deferred in 修复轮1 since it was judged a lesser
      // "stale metadata" concern — re-review found it's actually a REAL
      // data-loss path: `revision`/`persistedRevision` both reset to 0 on
      // every `loadCanvas()`, so small integers collide across mounts
      // constantly. If an OLD mount's `doSave` resolves AFTER a NEWER mount
      // has loaded and made its own edit that happens to land on the SAME
      // `revision` number, the old save's unconditional
      // `persistedRevision: snapshot.revision` would mark the NEW mount's
      // real, unsaved edit as "already saved" — the next `doSave`/
      // `flushSave` hits the `persistedRevision >= revision` early-return
      // above and the edit is silently never sent. Captured HERE (same
      // synchronous snapshot point as `revision`/`baseUpdatedAt` below) so
      // it reflects exactly which mount generation took this snapshot.
      const epochAtSnapshot = get().mountEpoch;

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
          nodes_json: liveNodes.map(stripRfInternals),
          connections_json: state.connections,
          node_ops_json: state.nodeOps,
          connection_ops_json: state.connectionOps,
        },
      };

      set({ saveStatus: 'saving', saveError: null, saveErrorStatus: null });

      let result;
      try {
        result = await saveImpl(state.canvasId, snapshot.payload);
      } catch (err) {
        // 403 is not a failure to retry — it's a standing fact about this
        // session's rights. Latch read-only and land on a CLEAN save status:
        // the badge's read-only branch takes over, and nothing re-arms the
        // debounce. Epoch-guarded like every other post-await write below so
        // a stale mount's refusal can't mute a newer mount's writable load.
        if (isForbidden(err)) {
          if (get().mountEpoch !== epochAtSnapshot) return;
          set({
            readOnly: true,
            saveStatus: 'idle',
            saveError: null,
            saveErrorStatus: null,
            // Retire the "unsaved edits" signal along with the ability to
            // save: `applyRemoteUpdate` reads `revision > persistedRevision`
            // to decide dirty-vs-clean, and edits that can NEVER be persisted
            // must not make an incoming realtime row look like a conflict —
            // a viewer has no way to resolve that dialog. Clean state means
            // the newer row simply rebases, which is the truth they should
            // be looking at.
            persistedRevision: get().revision,
          });
          return;
        }
        // Keep the status alongside the message: it is the ONLY thing that
        // distinguishes "the server refused this" from "the request never
        // got there", and it stops existing the moment this catch returns.
        const message = err instanceof Error ? err.message : String(err);
        set({ saveStatus: 'error', saveError: message, saveErrorStatus: readErrorStatus(err) });
        return;
      }

      // The save request itself already happened (server has the payload
      // either way) — this guard only decides whether THIS mount is still
      // around to accept the local metadata write it implies. A stale
      // mount's tail finding the world has moved on is not an error: the
      // server-side write is not lost, only this generation's local
      // bookkeeping of it is skipped (the newer mount's own next save,
      // whenever it fires, carries the real current state anyway).
      if (get().mountEpoch !== epochAtSnapshot) return;

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
          saveErrorStatus: null,
          persistedRevision: snapshot.revision,
        });
        if (!accepted) scheduleSave();
      } else {
        // 409: surface the server row, freeze auto-save until the user
        // resolves. Guarded the same way — an OLD mount's conflict must
        // never paint a false conflict banner over a NEWER mount's clean,
        // already-successful state.
        set({
          saveStatus: 'error',
          saveError: CONFLICT_SAVE_ERROR,
          saveErrorStatus: 409,
          conflict: result.conflict,
        });
      }
    }

    return {
      canvasId: null,
      kind: null,
      name: null,
      projectId: null,
      episodeId: null,
      assetId: null,
      loadStatus: 'idle',
      loadError: null,
      mountEpoch: 0,
      viewportEpoch: 0,
      viewport: IDENTITY_VIEWPORT,
      nodes: [],
      connections: [],
      nodeOps: [],
      connectionOps: [],
      baseUpdatedAt: null,
      saveStatus: 'idle',
      saveError: null,
      saveErrorStatus: null,
      readOnly: false,
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
        // Closure state, so the `set({...})` below cannot reach it. Leaving
        // it set hands "there are unclaimed drag positions in `nodes`" to a
        // store that is no longer holding a canvas — and leaving mid-drag is
        // both the gesture the flag exists for and the thing that calls
        // `reset()`, so the two meet in practice.
        unclaimedDragTick = false;
        set({
          canvasId: null,
          kind: null,
          name: null,
          projectId: null,
          episodeId: null,
          assetId: null,
          loadStatus: 'idle',
          loadError: null,
          viewport: IDENTITY_VIEWPORT,
          viewportEpoch: (viewportEpochCounter += 1),
          nodes: [],
          connections: [],
          nodeOps: [],
          connectionOps: [],
          baseUpdatedAt: null,
          saveStatus: 'idle',
          saveError: null,
          saveErrorStatus: null,
          readOnly: false,
          conflict: null,
          revision: 0,
          persistedRevision: 0,
          selection: [],
          historyPast: [],
          historyFuture: [],
        });
      },

      async loadCanvas(canvasId: string) {
        // Bump BEFORE the first `await` (synchronous prefix of an async
        // function) — a caller that reads `mountEpoch` right after invoking
        // `loadCanvas()` (without awaiting it) sees the up-to-date value.
        mountEpochCounter += 1;
        set({ loadStatus: 'loading', loadError: null, mountEpoch: mountEpochCounter });
        try {
          const row = await loadImpl(canvasId);
          // `fromLoad` — this response is the authoritative permission
          // statement for the canvas being opened (see applyServerRow).
          applyServerRow(row, { fromLoad: true });
        } catch (err) {
          const message = err instanceof Error ? err.message : String(err);
          set({ loadStatus: 'error', loadError: message });
        }
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

      appendElementsNoHistory(nodes, connections) {
        if (nodes.length === 0 && connections.length === 0) return;
        const s = get();
        // Write-point duplicate-id guard (2026-08-12 incident): whatever a
        // caller's diff concluded, appending an id that already exists
        // would blank the surface — refuse it HERE, at the single place
        // runtime appends happen, not just in callers' lookup logic.
        let toAppend = nodes;
        if (nodes.length > 0) {
          const existingIds = new Set<string>();
          for (const n of s.nodes) {
            const id = (n as Record<string, unknown>).id;
            if (typeof id === 'string') existingIds.add(id);
          }
          toAppend = nodes.filter((n) => {
            const id = (n as Record<string, unknown>).id;
            if (typeof id !== 'string') return true;
            if (existingIds.has(id)) {
              console.warn(
                `[canvasCoreStore] appendElementsNoHistory dropped duplicate node id ${id}`,
              );
              return false;
            }
            existingIds.add(id);
            return true;
          });
        }
        if (toAppend.length === 0 && connections.length === 0) return;
        set({
          nodes: toAppend.length ? [...s.nodes, ...toAppend] : s.nodes,
          connections: connections.length
            ? [...s.connections, ...connections]
            : s.connections,
        });
        markDirty();
      },

      dedupeNodes(ids) {
        if (ids.length === 0) return;
        const target = new Set(ids);
        const seen = new Set<string>();
        let dropped = false;
        const next = get().nodes.filter((n) => {
          const id = (n as Record<string, unknown>).id;
          if (typeof id !== 'string' || !target.has(id)) return true;
          if (seen.has(id)) {
            dropped = true;
            return false;
          }
          seen.add(id);
          return true;
        });
        if (!dropped) return;
        // No noteDocumentEditStarting — an automated repair is not an
        // undoable user edit (same contract as appendElementsNoHistory).
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
        // A drag still HELD when this runs (route leave / surface unmount
        // mid-pointer-down) never got its drag-end `setNodes`, so its
        // positions are in `nodes` with no revision bump behind them and
        // `doSave` would early-return on `persistedRevision >= revision`.
        // Claim them now — this is the one path that runs on unmount.
        if (unclaimedDragTick) markDirty();
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
        set({ conflict: null, saveStatus: 'idle', saveError: null, saveErrorStatus: null });
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
        // Mid-drag: update positions ONLY. No history timer, and — since
        // canvas fluency Task 6 — no `markDirty()` either.
        //
        // A drag is one edit, not one edit per frame. The drag-end
        // `setNodes()` call marks dirty with the FINAL positions and starts
        // the 250ms history commit, so a per-tick dirty bought nothing: it
        // only bumped `revision` ~120 times per two-second drag, re-armed
        // the 500ms save debounce on every frame, and flickered the save
        // badge while the user was still holding the mouse. React Flow
        // always closes a drag with a `dragging: false` position change
        // (@xyflow/system XYDrag `end`, including its abort branch), which
        // the surface routes to `setNodes` — so there is no drag that ends
        // without one.
        //
        // `flushSave()` (route leave / surface unmount) still carries these
        // positions — that is what `unclaimedDragTick` is for, since a drag
        // interrupted by an unmount never reaches its drag-end `setNodes`.
        unclaimedDragTick = true;
        set({ nodes });
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

      setViewportSettled(viewport: CanvasViewport) {
        // One gesture, one write, one dirty signal. No history entry —
        // panning is navigation, not a document edit (markDirty does not
        // touch history).
        set({
          // viewportEpoch: intentionally NOT bumped — this value came FROM
          // React Flow and is already on screen; announcing it would push it
          // straight back and fight the user mid-gesture. The source scan in
          // canvasCoreStore.viewport.test.ts reads this marker.
          viewport: { ...viewport, zoom: clampZoom(viewport.zoom) },
        });
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
        //
        // `unclaimedDragTick` is the second term because a drag in flight no
        // longer moves `revision` (Task 6). Without it the rebase below runs
        // UNDER THE USER'S POINTER: `applyServerRow` replaces `nodes`, bumps
        // `viewportEpoch` (which `CanvasPage` answers by calling React Flow's
        // own `setViewport` mid-gesture) and clears the history stacks along
        // with the pre-drag base `noteDragStart()` just captured. A live drag
        // is unsaved local work, exactly as it was before Task 6.
        if (s.revision > s.persistedRevision || unclaimedDragTick) {
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
