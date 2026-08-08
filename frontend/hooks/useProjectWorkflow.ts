/**
 * useProjectWorkflow — single fetch of a project's workflow instance shared by
 * the workspace shell (top-bar agents chip + advance gate), the Overview
 * (strip + current node card) and the sidebar Stages group. Keeping one source
 * of truth means an advance/patch reload refreshes every surface at once.
 *
 * `episodeId` gate (B6 PR-2 task 10): the backend's 4 episode-aware endpoints
 * made `episode_id` REQUIRED (a bare project-level read now 422s). But
 * `ProjectWorkspace` resets `currentEpisodeId` to `null` whenever `project.id`
 * changes and only resolves it once its own episodes-progress fetch settles —
 * so this hook's inputs pass through a null episodeId on every first paint.
 * Skip the fetch entirely while that's true (stay in the loading state, keep
 * `workflow` null) rather than firing a request the server will reject; the
 * effect re-runs and fetches for real once episodes resolve a real id.
 */

import { useCallback, useEffect, useState } from 'react';
import { fetchProjectWorkflow } from '../services/workflowService';
import type { ProjectWorkflow } from '../types';

interface UseProjectWorkflow {
  workflow: ProjectWorkflow | null;
  loading: boolean;
  reload: () => Promise<void>;
}

export function useProjectWorkflow(
  projectId: string,
  episodeId?: string | null,
): UseProjectWorkflow {
  const [workflow, setWorkflow] = useState<ProjectWorkflow | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    if (!episodeId) return;
    try {
      const wf = await fetchProjectWorkflow(projectId, episodeId);
      setWorkflow(wf);
    } catch (err) {
      console.error('[useProjectWorkflow] fetch failed', err);
      setWorkflow(null);
    } finally {
      setLoading(false);
    }
  }, [projectId, episodeId]);

  useEffect(() => {
    if (!episodeId) {
      // No episode resolved yet — stay in the loading/empty state instead of
      // firing a request the server would 422 on.
      setWorkflow(null);
      setLoading(true);
      return;
    }
    let alive = true;
    setLoading(true);
    fetchProjectWorkflow(projectId, episodeId)
      .then((wf) => {
        if (alive) setWorkflow(wf);
      })
      .catch((err) => {
        console.error('[useProjectWorkflow] fetch failed', err);
        if (alive) setWorkflow(null);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [projectId, episodeId]);

  return { workflow, loading, reload };
}
