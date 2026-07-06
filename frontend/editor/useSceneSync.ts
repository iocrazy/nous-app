/**
 * useSceneSync — the optimistic op queue for one scene (spec v3 §3.4 D4/D5).
 *
 * A single hook instance owns exactly one scene's write path. Edits arrive as
 * `dispatchOps(ops, optimistic)`: the optimistic element list is shown at once
 * and the op batch joins a FIFO queue that is flushed **strictly serially** —
 * there is never more than one `applyOps` in flight per hook, so the server's
 * content_version can be advanced one batch at a time without racing.
 *
 * The interesting behaviour is failure handling, all driven off the typed
 * errors from sceneService:
 *  - 409 whose server elements deep-equal our optimistic result → the write
 *    already landed (identical concurrent edit); silently adopt the version and
 *    replay the rest of the queue. The user never sees a conflict.
 *  - 409 with divergent content → freeze the queue, surface conflict{mine,theirs}
 *    and let the caller resolve it ('mine' replays our elements as full-payload
 *    upserts on top of theirs; 'theirs' drops our pending work and adopts the
 *    server).
 *  - a plain network reject → exponential backoff (1s/2s/4s) retrying the SAME
 *    batch, or 'offline' when navigator.onLine is false (resume on 'online').
 *  - 422 (OpRejectedError) → never swallowed: log with context, drop the bad
 *    batch and refetch the scene as truth.
 *
 * `flush()` resolves once the queue drains — OR once syncing halts on a
 * 'conflict'/'offline' state (otherwise the branch-switch guard would block on
 * unbounded user interaction). Guards must re-check `saveState` after awaiting:
 * 'saved' means everything landed; 'conflict'/'offline' means work is pending
 * and the guard should prompt instead of navigating.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  applyOps as applyOpsApi,
  getScene as getSceneApi,
  OpRejectedError,
  VersionConflictError,
} from './sceneService';
import { applyLocal } from './opBuilder';
import type { ElementOp, ScriptElement, SceneDoc } from './types';

export type SaveState = 'saved' | 'saving' | 'retrying' | 'conflict' | 'offline';

/**
 * One `script_ops` row as delivered by Supabase Realtime (Phase B P5 / C2).
 * `op_seq` equals the scene's content_version AFTER the op applied; `actor` is
 * the writer's user uuid (or 'copilot'); `op_json.ops` is the forward batch.
 */
export interface RemoteOpRow {
  scene_id: string;
  op_seq: number;
  actor: string;
  op_json: { ops?: ElementOp[]; inverse?: ElementOp[] } | null;
}

export interface SceneSync {
  elements: ScriptElement[];
  version: number;
  saveState: SaveState;
  conflict: { mine: ScriptElement[]; theirs: ScriptElement[] } | null;
  dispatchOps(ops: ElementOp[], optimistic: ScriptElement[]): void;
  resolveConflict(choice: 'mine' | 'theirs'): void;
  /** Apply (or reconcile from) a remote op row streamed for THIS scene (C2). */
  applyRemoteOps(row: RemoteOpRow): void;
  flush(): Promise<void>;
}

export interface SceneSyncOptions {
  /** Local user's actor id, so applyRemoteOps can drop self-echoed rows. */
  selfActorId?: string | null;
}

interface Batch {
  ops: ElementOp[];
  optimistic: ScriptElement[];
  attempt: number;
}

const BACKOFF_BASE_MS = 1000;
const BACKOFF_CAP_MS = 4000;

// Field-wise canonical compare: JSON.stringify would break on key-order or
// backend normalization differences (e.g. read-back injecting character_id:
// null), turning a byte-identical concurrent write into a spurious conflict.
const sameElement = (a: ScriptElement, b: ScriptElement): boolean =>
  a.id === b.id &&
  a.type === b.type &&
  a.text === b.text &&
  (a.character_id ?? null) === (b.character_id ?? null);

const sameElements = (a: ScriptElement[], b: ScriptElement[]): boolean =>
  a.length === b.length && a.every((el, i) => sameElement(el, b[i]));

/**
 * Rebuild `mine` as full-payload insert-upserts (ids preserved, order anchored).
 *
 * Known limitation (accepted for Phase 1, spec §3.4's dominant case is text
 * divergence on the SAME elements): upserts never move or delete, so a
 * reorder conflict keeps theirs' positions and server-only elements survive
 * "Keep mine". A true mine-wins (move/delete emission) belongs with the
 * version/diff UI in Phase 4.
 */
function upsertOpsFor(mine: ScriptElement[]): ElementOp[] {
  return mine.map((el, i) => ({
    op: 'insert' as const,
    element_id: el.id,
    after_id: i > 0 ? mine[i - 1].id : null,
    payload: { type: el.type, text: el.text, character_id: el.character_id ?? null },
  }));
}

export function useSceneSync(scene: SceneDoc, options?: SceneSyncOptions): SceneSync {
  const [elements, setElements] = useState<ScriptElement[]>(scene.elements);
  const [version, setVersion] = useState<number>(scene.content_version);
  const [saveState, setSaveState] = useState<SaveState>('saved');
  const [conflict, setConflict] = useState<{ mine: ScriptElement[]; theirs: ScriptElement[] } | null>(
    null,
  );

  const queueRef = useRef<Batch[]>([]);
  const flushingRef = useRef(false);
  const frozenRef = useRef(false);
  const offlineRef = useRef(false);
  const versionRef = useRef(scene.content_version);
  const elementsRef = useRef(scene.elements);
  const conflictVersionRef = useRef(0);
  const conflictTheirsRef = useRef<ScriptElement[]>([]);
  const flushWaitersRef = useRef<Array<() => void>>([]);
  const sceneIdRef = useRef(scene.id);
  const selfActorIdRef = useRef(options?.selfActorId ?? null);
  selfActorIdRef.current = options?.selfActorId ?? null;
  const mountedRef = useRef(true);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pumpRef = useRef<() => Promise<void>>(async () => {});

  // Cancellable backoff: unmount clears the timer so a pending retry never
  // fires a network call on a dead hook (the unresolved promise is GC'd with
  // its closure).
  const sleep = useCallback(
    (ms: number): Promise<void> =>
      new Promise((resolve) => {
        retryTimerRef.current = setTimeout(() => {
          retryTimerRef.current = null;
          resolve();
        }, ms);
      }),
    [],
  );

  // Reset all state when the hook is pointed at a different scene.
  useEffect(() => {
    if (sceneIdRef.current === scene.id) return;
    sceneIdRef.current = scene.id;
    queueRef.current = [];
    flushingRef.current = false;
    frozenRef.current = false;
    offlineRef.current = false;
    versionRef.current = scene.content_version;
    elementsRef.current = scene.elements;
    setElements(scene.elements);
    setVersion(scene.content_version);
    setSaveState('saved');
    setConflict(null);
  }, [scene.id, scene.content_version, scene.elements]);

  const releaseFlushWaiters = useCallback(() => {
    const waiters = flushWaitersRef.current;
    flushWaitersRef.current = [];
    waiters.forEach((resolve) => resolve());
  }, []);

  const settleFlush = useCallback(() => {
    if (queueRef.current.length === 0 && !flushingRef.current) {
      releaseFlushWaiters();
    }
  }, [releaseFlushWaiters]);

  const pump = useCallback(async () => {
    if (flushingRef.current || frozenRef.current || offlineRef.current) return;
    flushingRef.current = true;
    try {
      while (queueRef.current.length > 0 && !frozenRef.current && !offlineRef.current) {
        const batch = queueRef.current[0];
        if (mountedRef.current) setSaveState('saving');
        try {
          const res = await applyOpsApi(sceneIdRef.current, batch.ops, versionRef.current);
          versionRef.current = res.content_version;
          queueRef.current.shift();
          if (mountedRef.current) setVersion(res.content_version);
        } catch (err) {
          if (err instanceof VersionConflictError) {
            if (sameElements(err.elements, batch.optimistic)) {
              // Identical concurrent write already landed — adopt + replay rest.
              versionRef.current = err.currentVersion;
              queueRef.current.shift();
              if (mountedRef.current) setVersion(err.currentVersion);
              continue;
            }
            // Divergent — freeze and surface for the user to resolve.
            frozenRef.current = true;
            conflictVersionRef.current = err.currentVersion;
            conflictTheirsRef.current = err.elements;
            if (mountedRef.current) {
              setConflict({ mine: elementsRef.current, theirs: err.elements });
              setSaveState('conflict');
            }
            // Syncing halted on user input — release flush() waiters so the
            // branch-switch guard can prompt (docstring contract).
            releaseFlushWaiters();
            break;
          }
          if (err instanceof OpRejectedError) {
            // 422: never swallowed. Log with context, drop the bad batch, and
            // refetch the scene as the source of truth.
            console.error('[useSceneSync] op rejected (422)', {
              sceneId: sceneIdRef.current,
              code: err.code,
              detail: err.detail,
              ops: batch.ops,
            });
            queueRef.current.shift();
            try {
              const fresh = await getSceneApi(sceneIdRef.current);
              versionRef.current = fresh.content_version;
              elementsRef.current = fresh.elements;
              if (mountedRef.current) {
                setVersion(fresh.content_version);
                setElements(fresh.elements);
              }
            } catch (refetchErr) {
              console.error('[useSceneSync] refetch after 422 failed', refetchErr);
            }
            continue;
          }
          // Plain network error — offline vs. retry-with-backoff.
          if (typeof navigator !== 'undefined' && navigator.onLine === false) {
            offlineRef.current = true;
            if (mountedRef.current) setSaveState('offline');
            releaseFlushWaiters(); // halted on connectivity — same guard contract
            break;
          }
          const delay = Math.min(BACKOFF_BASE_MS * 2 ** batch.attempt, BACKOFF_CAP_MS);
          batch.attempt += 1;
          if (mountedRef.current) setSaveState('retrying');
          await sleep(delay);
          continue; // retry the same batch (still at queue head)
        }
      }
      if (
        !frozenRef.current &&
        !offlineRef.current &&
        queueRef.current.length === 0 &&
        mountedRef.current
      ) {
        setSaveState('saved');
      }
    } finally {
      flushingRef.current = false;
      // A dispatch that raced the loop exit gets picked up here.
      if (queueRef.current.length > 0 && !frozenRef.current && !offlineRef.current) {
        void pumpRef.current();
      } else {
        settleFlush();
      }
    }
  }, [settleFlush, releaseFlushWaiters, sleep]);

  pumpRef.current = pump;

  const dispatchOps = useCallback(
    (ops: ElementOp[], optimistic: ScriptElement[]) => {
      elementsRef.current = optimistic;
      setElements(optimistic);
      // While frozen/offline nothing flushes — keep the truthful indicator
      // ('conflict'/'offline') instead of flashing a false 'saving'.
      if (!frozenRef.current && !offlineRef.current) setSaveState('saving');
      queueRef.current.push({ ops, optimistic, attempt: 0 });
      void pump();
    },
    [pump],
  );

  const resolveConflict = useCallback(
    (choice: 'mine' | 'theirs') => {
      frozenRef.current = false;
      setConflict(null);
      if (choice === 'theirs') {
        // Drop everything pending and adopt the server's view.
        queueRef.current = [];
        versionRef.current = conflictVersionRef.current;
        elementsRef.current = conflictTheirsRef.current;
        setVersion(conflictVersionRef.current);
        setElements(conflictTheirsRef.current);
        setSaveState('saved');
        settleFlush();
        return;
      }
      // 'mine': rebuild our elements as upserts on top of the server base.
      const mine = elementsRef.current;
      queueRef.current = [{ ops: upsertOpsFor(mine), optimistic: mine, attempt: 0 }];
      versionRef.current = conflictVersionRef.current;
      setVersion(conflictVersionRef.current);
      setSaveState('saving');
      void pump();
    },
    [pump, settleFlush],
  );

  // Refetch the scene as the source of truth after a remote gap. When local
  // edits are pending (`hadPending`) the queue is already frozen by the caller;
  // we surface the server's version as `theirs` and never clobber our optimistic
  // work (canvasCoreStore guard ③ semantics). Otherwise we adopt the server view
  // and re-open the queue so any keystrokes typed during the await still flush.
  const reconcileFromServer = useCallback(
    async (hadPending: boolean) => {
      let fresh: SceneDoc;
      try {
        fresh = await getSceneApi(sceneIdRef.current);
      } catch (err) {
        console.error('[useSceneSync] remote reconcile refetch failed', {
          sceneId: sceneIdRef.current,
          err,
        });
        return;
      }
      if (!mountedRef.current) return;
      if (hadPending) {
        conflictVersionRef.current = fresh.content_version;
        conflictTheirsRef.current = fresh.elements;
        setConflict({ mine: elementsRef.current, theirs: fresh.elements });
        setSaveState('conflict');
        // Halted on user input — release flush() waiters (docstring contract).
        releaseFlushWaiters();
      } else {
        frozenRef.current = false;
        versionRef.current = fresh.content_version;
        elementsRef.current = fresh.elements;
        setVersion(fresh.content_version);
        setElements(fresh.elements);
        setSaveState('saved');
        if (queueRef.current.length > 0) void pumpRef.current();
      }
    },
    [releaseFlushWaiters],
  );

  // Apply a remote op row for THIS scene (C2). Three guards, in order:
  //  1. self-echo (our own actor) or stale (op_seq already applied) → ignore.
  //  2. exact next seq on a CLEAN local state → splice the forward ops onto the
  //     optimistic view and advance the version (no network round-trip).
  //  3. gap / malformed / local dirty → freeze and reconcile from the server;
  //     a dirty local state diverges into the existing conflict UX.
  const applyRemoteOps = useCallback(
    (row: RemoteOpRow) => {
      const selfId = selfActorIdRef.current;
      if (selfId && row.actor === selfId) return; // self-echo
      if (typeof row.op_seq !== 'number' || row.op_seq <= versionRef.current) return; // stale

      const ops = row.op_json?.ops;
      const contiguous = row.op_seq === versionRef.current + 1;
      const clean =
        queueRef.current.length === 0 && !frozenRef.current && !offlineRef.current;

      if (contiguous && clean && Array.isArray(ops)) {
        const next = applyLocal(elementsRef.current, ops);
        elementsRef.current = next;
        versionRef.current = row.op_seq;
        if (mountedRef.current) {
          setElements(next);
          setVersion(row.op_seq);
        }
        return;
      }

      // Guard 3: reconcile from truth. Freeze first so an in-flight pump cannot
      // advance the version underneath the refetch. `hadPending` decides whether
      // we diverge (conflict) or cleanly adopt.
      const hadPending = queueRef.current.length > 0 || frozenRef.current;
      frozenRef.current = true;
      void reconcileFromServer(hadPending);
    },
    [reconcileFromServer],
  );

  const flush = useCallback((): Promise<void> => {
    if (queueRef.current.length === 0 && !flushingRef.current) return Promise.resolve();
    return new Promise<void>((resolve) => {
      flushWaitersRef.current.push(resolve);
    });
  }, []);

  // Resume the queue when connectivity comes back.
  useEffect(() => {
    mountedRef.current = true;
    const onOnline = () => {
      if (!offlineRef.current) return;
      offlineRef.current = false;
      // Retry the frozen batch from a clean backoff.
      if (queueRef.current[0]) queueRef.current[0].attempt = 0;
      if (mountedRef.current) setSaveState('saving');
      void pumpRef.current();
    };
    window.addEventListener('online', onOnline);
    return () => {
      mountedRef.current = false;
      window.removeEventListener('online', onOnline);
      if (retryTimerRef.current !== null) {
        clearTimeout(retryTimerRef.current);
        retryTimerRef.current = null;
      }
    };
  }, []);

  return {
    elements,
    version,
    saveState,
    conflict,
    dispatchOps,
    resolveConflict,
    applyRemoteOps,
    flush,
  };
}
