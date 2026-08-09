/**
 * useSceneShots — fetch a script's scenes plus every scene's shots in
 * parallel (StoryboardView's mount-load pattern, lifted so both triview
 * consumers — the scene-card board (Task 1) and the flat shot table
 * (Task 2) — share one fetch instead of re-implementing it).
 *
 * Snowflake ids are strings end-to-end; `shotsByScene` keys are always
 * `String(scene.id)` (#1006 convention, mirrored from StoryboardView).
 */
import { useCallback, useEffect, useState } from 'react';
import { listScenes, listShots, type Shot } from '../../editor/sceneService';
import type { SceneDoc } from '../../editor/types';

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
  const [scenes, setScenes] = useState<SceneDoc[]>([]);
  const [shotsByScene, setShotsByScene] = useState<Record<string, Shot[]>>({});
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    listScenes(scriptId)
      .then(async (loadedScenes) => {
        if (cancelled) return;
        setScenes(loadedScenes);
        const pairs = await Promise.all(
          loadedScenes.map(async (scene) => {
            const shots = await listShots(scene.id).catch((err) => {
              console.error('[useSceneShots] failed to load shots', err);
              return [] as Shot[];
            });
            return [String(scene.id), shots] as const;
          }),
        );
        if (!cancelled) setShotsByScene(Object.fromEntries(pairs));
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

  const refreshScene = useCallback(async (sceneId: string) => {
    try {
      const shots = await listShots(sceneId);
      setShotsByScene((prev) => ({ ...prev, [String(sceneId)]: shots }));
    } catch (err) {
      console.error('[useSceneShots] failed to reload scene shots', err);
    }
  }, []);

  const setShotsForScene = useCallback((sceneId: string, shots: Shot[]) => {
    setShotsByScene((prev) => ({ ...prev, [String(sceneId)]: shots }));
  }, []);

  return { scenes, shotsByScene, loading, refreshScene, setShotsForScene };
}
