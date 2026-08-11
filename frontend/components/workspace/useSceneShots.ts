/**
 * useSceneShots — fetch a script's scenes plus every scene's shots in
 * parallel (StoryboardView's mount-load pattern, lifted so both triview
 * consumers — the scene-card board (Task 1) and the flat shot table
 * (Task 2) — share one fetch instead of re-implementing it).
 *
 * Snowflake ids are strings end-to-end; `shotsByScene` keys are always
 * `String(scene.id)` (#1006 convention, mirrored from StoryboardView).
 *
 * Fix 2 (storyboard-page-polish, stale-while-revalidate): re-entering the
 * same script's storyboard used to re-run this whole fetch and show
 * `loading` again on every mount. A module-level cache keyed by scriptId
 * (mirrors `settledCache` in `agentActivity/useRunToolActivity.ts`) renders
 * the last-known scenes/shots immediately on a cached mount and skips the
 * loading flag; the fetch still runs in the background to refresh the cache
 * and correct the rendered state if it changed.
 *
 * `onStoryboardRefresh` subscription (Task 6 review 修复轮1, 2026-08-11):
 * the OLD editor storyboard rail's `StoryboardView` subscribed to this same
 * channel so an Agent Run Undo (`useRunUndo.ts`'s `requestStoryboardRefresh`)
 * could silently re-pull scenes/shots while it was mounted. Deleting
 * `StoryboardView` in Task 6 orphaned that channel — nobody subscribed
 * anymore, so an undo left the storyboard page showing pre-undo data with no
 * error and no signal. Re-homed HERE (the data hook itself) rather than in
 * `EpisodeStoryboardPage` because both view-one consumers (`EpisodeSceneBoard`
 * AND `EpisodeShotListTable`) already share this one hook — subscribing once
 * here covers both for free, and the subscription's mount/unmount lifecycle
 * naturally matches "is anyone currently looking at scene/shot data for this
 * script" (narrower than, and a strict subset of, the storyboard PAGE's own
 * lifetime — when neither view is the active tab, e.g. Canvas is, there is
 * no consumer and the refresh is a no-op, same "nobody's listening" contract
 * the bus already documents). The storyboard CANVAS's own reconcile re-run
 * is a separate concern this hook can't reach (different store) — see
 * `EpisodeStoryboardPage.tsx`'s own `onStoryboardRefresh` subscription for
 * that half.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { listScenes, listShots, type Shot } from '../../editor/sceneService';
import type { SceneDoc } from '../../editor/types';
import { onStoryboardRefresh } from '../agentActivity/shotFocusBus';

interface SceneShotsSnapshot {
  scenes: SceneDoc[];
  shotsByScene: Record<string, Shot[]>;
}

/** scriptId -> last-fetched scenes + shots. */
const sceneShotsCache = new Map<string, SceneShotsSnapshot>();

/** Exposed for tests/invalidation — module-level cache otherwise leaks between cases. */
export function __clearSceneShotsCache(scriptId?: string): void {
  if (scriptId) sceneShotsCache.delete(scriptId);
  else sceneShotsCache.clear();
}

function snapshotsEqual(a: SceneShotsSnapshot, b: SceneShotsSnapshot): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

export interface UseSceneShots {
  scenes: SceneDoc[];
  shotsByScene: Record<string, Shot[]>;
  loading: boolean;
  /** Re-fetch one scene's shots (e.g. after an edit outside the poll path). */
  refreshScene: (sceneId: string) => Promise<void>;
  /** Overwrite one scene's shot list in local state (poll settlement). */
  setShotsForScene: (sceneId: string, shots: Shot[]) => void;
}

export function useSceneShots(scriptId: string): UseSceneShots {
  const cached = sceneShotsCache.get(scriptId);
  const [scenes, setScenes] = useState<SceneDoc[]>(() => cached?.scenes ?? []);
  const [shotsByScene, setShotsByScene] = useState<Record<string, Shot[]>>(
    () => cached?.shotsByScene ?? {},
  );
  const [loading, setLoading] = useState(() => !cached);

  // Generation guard shared by both fetch triggers below (mount + the
  // onStoryboardRefresh subscription): a request superseded by a NEWER one
  // (scriptId changed mid-flight, or two refresh triggers overlap) must not
  // let its stale result win. Mirrors the `cancelled`/`sameCanvas()` pattern
  // this epic already uses elsewhere (CanvasPage.tsx's reconcile effect).
  // `epochRef` alone already discards a superseded call (any NEW `fetchAll`
  // invocation bumps it past whatever's in flight) — it needs no reset of
  // its own, it just keeps counting up for the component's whole life.
  // `mountedRef` exists for the DIFFERENT case epoch can't cover: a genuine
  // final unmount with no new fetch ever started to bump epoch past the
  // in-flight one.
  const epochRef = useRef(0);
  // Task 6 review 修复轮2 (2026-08-11): a `useRef(true)` initializer plus a
  // CLEANUP-ONLY effect (`useEffect(() => () => { mountedRef.current =
  // false }, [])`) is broken under React 18 StrictMode's dev-only double-
  // invoke (`index.tsx`'s app-wide `<React.StrictMode>`) — mount → cleanup
  // (flips false) → remount runs the effect body again, but an EMPTY body
  // never flips it back to true, so it stays false FOREVER after the very
  // first StrictMode cycle. Every subsequent `fetchAll` then bails on its
  // own `!mountedRef.current` check before ever reaching `setLoading(false)`
  // — view one/three spin forever in `npm run dev` (confirmed by RTL under
  // `<React.StrictMode>`; production builds don't double-invoke, so this
  // never showed up in a build, only in the actual dev-server manual-test
  // workflow). Fix: the effect BODY resets it to true too — mount sets
  // true, cleanup sets false, StrictMode's forced extra mount/cleanup/mount
  // cycle nets out correctly at true, same as every other ref that toggles
  // across a mount effect in this codebase.
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // Shared fetch-all body — extracted so both the mount effect AND the
  // onStoryboardRefresh subscription below run the exact same logic rather
  // than two hand-rolled copies drifting apart.
  const fetchAll = useCallback(
    async (opts: { showLoading: boolean }) => {
      const myEpoch = ++epochRef.current;
      const hadCache = sceneShotsCache.has(scriptId);
      if (opts.showLoading) setLoading(!hadCache);
      try {
        const loadedScenes = await listScenes(scriptId);
        if (!mountedRef.current || epochRef.current !== myEpoch) return;
        const pairs = await Promise.all(
          loadedScenes.map(async (scene) => {
            const shots = await listShots(scene.id).catch((err) => {
              console.error('[useSceneShots] failed to load shots', err);
              return [] as Shot[];
            });
            return [String(scene.id), shots] as const;
          }),
        );
        if (!mountedRef.current || epochRef.current !== myEpoch) return;
        const fresh: SceneShotsSnapshot = {
          scenes: loadedScenes,
          shotsByScene: Object.fromEntries(pairs),
        };
        const prevCached = sceneShotsCache.get(scriptId);
        sceneShotsCache.set(scriptId, fresh);
        // Only touch state when the fresh result actually differs, so an
        // already-rendered board (which may have local poll-settled shot
        // updates layered on top via `setShotsForScene`) doesn't get
        // clobbered by a no-op refresh.
        if (hadCache && prevCached && snapshotsEqual(prevCached, fresh)) return;
        setScenes(fresh.scenes);
        setShotsByScene(fresh.shotsByScene);
      } catch (err) {
        console.error('[useSceneShots] failed to load scenes', err);
      } finally {
        if (mountedRef.current && epochRef.current === myEpoch) setLoading(false);
      }
    },
    [scriptId],
  );

  useEffect(() => {
    void fetchAll({ showLoading: true });
  }, [fetchAll]);

  // Off-canvas write refresh (see the file doc comment above) — no loading
  // flash (mirrors the OLD StoryboardView's `void loadAll().then(setShotsByScene)`
  // UX exactly: silently re-fetch and let the diff above decide whether
  // anything actually changed).
  useEffect(
    () =>
      onStoryboardRefresh(() => {
        void fetchAll({ showLoading: false });
      }),
    [fetchAll],
  );

  const refreshScene = useCallback(
    async (sceneId: string) => {
      try {
        const shots = await listShots(sceneId);
        setShotsByScene((prev) => {
          const next = { ...prev, [String(sceneId)]: shots };
          const prevCached = sceneShotsCache.get(scriptId);
          if (prevCached) sceneShotsCache.set(scriptId, { ...prevCached, shotsByScene: next });
          return next;
        });
      } catch (err) {
        console.error('[useSceneShots] failed to reload scene shots', err);
      }
    },
    [scriptId],
  );

  // Poll-settlement path (Auto Storyboard) — logic untouched; only mirrors
  // the same update into the module cache so a later stale-while-revalidate
  // mount doesn't briefly regress behind shots this tab already knows about.
  const setShotsForScene = useCallback(
    (sceneId: string, shots: Shot[]) => {
      setShotsByScene((prev) => {
        const next = { ...prev, [String(sceneId)]: shots };
        const prevCached = sceneShotsCache.get(scriptId);
        if (prevCached) sceneShotsCache.set(scriptId, { ...prevCached, shotsByScene: next });
        return next;
      });
    },
    [scriptId],
  );

  return { scenes, shotsByScene, loading, refreshScene, setShotsForScene };
}
