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
 */
import { useCallback, useEffect, useState } from 'react';
import { listScenes, listShots, type Shot } from '../../editor/sceneService';
import type { SceneDoc } from '../../editor/types';

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

  useEffect(() => {
    let cancelled = false;
    const hadCache = sceneShotsCache.has(scriptId);
    setLoading(!hadCache);
    listScenes(scriptId)
      .then(async (loadedScenes) => {
        if (cancelled) return;
        const pairs = await Promise.all(
          loadedScenes.map(async (scene) => {
            const shots = await listShots(scene.id).catch((err) => {
              console.error('[useSceneShots] failed to load shots', err);
              return [] as Shot[];
            });
            return [String(scene.id), shots] as const;
          }),
        );
        if (cancelled) return;
        const fresh: SceneShotsSnapshot = {
          scenes: loadedScenes,
          shotsByScene: Object.fromEntries(pairs),
        };
        const prevCached = sceneShotsCache.get(scriptId);
        sceneShotsCache.set(scriptId, fresh);
        // Background refresh (hadCache): only touch state when the fresh
        // result actually differs, so an already-rendered board (which may
        // have local poll-settled shot updates layered on top via
        // `setShotsForScene`) doesn't get clobbered by a no-op refresh.
        if (hadCache && prevCached && snapshotsEqual(prevCached, fresh)) return;
        setScenes(fresh.scenes);
        setShotsByScene(fresh.shotsByScene);
      })
      .catch((err) => {
        console.error('[useSceneShots] failed to load scenes', err);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [scriptId]);

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
