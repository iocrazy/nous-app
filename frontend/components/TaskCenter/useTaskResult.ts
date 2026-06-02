import { useEffect, useState } from 'react';
import type { UnifiedTask } from '../../contexts/TaskManagerContext';
import { taskResultKind, type ResultKind } from './taskResultKind';
import { fetchResourceById } from '../../services/resourceService';
import {
  getTranscriptByResource,
  getSummaryByResource,
  getVisualAnalysisByResource,
} from '../../services/aiService';

export interface TaskResultState {
  kind: ResultKind;
  loading: boolean;
  error: string | null;
  /** resource (media) / transcript / summary payload — shape depends on kind. */
  data: unknown;
}

/**
 * Fetch the typed result payload for a task in the detail modal. media →
 * resource, transcript/summary → the ai_* getter; agent + generic render from
 * the task itself (no fetch). Vision (ai_extract) maps to generic until a read
 * endpoint exists.
 */
export function useTaskResult(task: UnifiedTask | null): TaskResultState {
  const kind = task ? taskResultKind(task) : 'generic';
  const [state, setState] = useState<{ loading: boolean; error: string | null; data: unknown }>({
    loading: false,
    error: null,
    data: null,
  });

  useEffect(() => {
    if (!task) return;
    if (kind === 'agent' || kind === 'generic' || !task.resource_id) {
      setState({ loading: false, error: null, data: null });
      return;
    }
    let cancelled = false;
    setState({ loading: true, error: null, data: null });
    const rid = String(task.resource_id);
    const run = async () => {
      try {
        let data: unknown = null;
        if (kind === 'media') data = await fetchResourceById(rid);
        else if (kind === 'transcript') data = await getTranscriptByResource(rid);
        else if (kind === 'summary') data = await getSummaryByResource(rid);
        else if (kind === 'vision') data = await getVisualAnalysisByResource(rid);
        if (!cancelled) setState({ loading: false, error: null, data });
      } catch (err) {
        if (!cancelled) {
          setState({
            loading: false,
            error: err instanceof Error ? err.message : 'Failed to load result',
            data: null,
          });
        }
      }
    };
    run();
    return () => {
      cancelled = true;
    };
  }, [task, kind]);

  return { kind, ...state };
}
