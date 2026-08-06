/**
 * useProjectWorkflow — single fetch of a project's workflow instance shared by
 * the workspace shell (top-bar agents chip + advance gate), the Overview
 * (strip + current node card) and the sidebar Stages group. Keeping one source
 * of truth means an advance/patch reload refreshes every surface at once.
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
    try {
      const wf = await fetchProjectWorkflow(projectId, episodeId ?? undefined);
      setWorkflow(wf);
    } catch (err) {
      console.error('[useProjectWorkflow] fetch failed', err);
      setWorkflow(null);
    } finally {
      setLoading(false);
    }
  }, [projectId, episodeId]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    fetchProjectWorkflow(projectId, episodeId ?? undefined)
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
