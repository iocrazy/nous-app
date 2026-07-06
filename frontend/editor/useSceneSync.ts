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
 * `flush()` resolves once the queue drains — the branch-switch guard (spec §3.6)
 * awaits it before navigating away.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  applyOps as applyOpsApi,
  getScene as getSceneApi,
  OpRejectedError,
  VersionConflictError,
} from './sceneService';
import type { ElementOp, ScriptElement, SceneDoc } from './types';

export type SaveState = 'saved' | 'saving' | 'retrying' | 'conflict' | 'offline';

export interface SceneSync {
  elements: ScriptElement[];
  version: number;
  saveState: SaveState;
  conflict: { mine: ScriptElement[]; theirs: ScriptElement[] } | null;
  dispatchOps(ops: ElementOp[], optimistic: ScriptElement[]): void;
  resolveConflict(choice: 'mine' | 'theirs'): void;
  flush(): Promise<void>;
}

interface Batch {
  ops: ElementOp[];
  optimistic: ScriptElement[];
  attempt: number;
}

const BACKOFF_BASE_MS = 1000;
const BACKOFF_CAP_MS = 4000;

const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

const sameElements = (a: ScriptElement[], b: ScriptElement[]): boolean =>
  JSON.stringify(a) === JSON.stringify(b);

/** Rebuild `mine` as full-payload insert-upserts (ids preserved, order anchored). */
function upsertOpsFor(mine: ScriptElement[]): ElementOp[] {
  return mine.map((el, i) => ({
    op: 'insert' as const,
    element_id: el.id,
    after_id: i > 0 ? mine[i - 1].id : null,
    payload: { type: el.type, text: el.text, character_id: el.character_id ?? null },
  }));
}

export function useSceneSync(scene: SceneDoc): SceneSync {
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
  const mountedRef = useRef(true);
  const pumpRef = useRef<() => Promise<void>>(async () => {});

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

  const settleFlush = useCallback(() => {
    if (queueRef.current.length === 0 && !flushingRef.current) {
      const waiters = flushWaitersRef.current;
      flushWaitersRef.current = [];
      waiters.forEach((resolve) => resolve());
    }
  }, []);

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
  }, [settleFlush]);

  pumpRef.current = pump;

  const dispatchOps = useCallback(
    (ops: ElementOp[], optimistic: ScriptElement[]) => {
      elementsRef.current = optimistic;
      setElements(optimistic);
      setSaveState('saving');
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
    };
  }, []);

  return { elements, version, saveState, conflict, dispatchOps, resolveConflict, flush };
}
