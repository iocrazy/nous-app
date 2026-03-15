import { useEffect } from 'react';
import { getSupabaseClient } from '../../supabaseClient';
import { useStoryboardStore } from '../../stores/storyboardStore';

// ─── Hook ─────────────────────────────────────────────────────────────────────

/**
 * Subscribes to Supabase realtime updates on the `unified_tasks` table,
 * filtered to the current project. On task status changes, updates the
 * corresponding node's data_json progress field in the store.
 */
export function useStoryboardRealtime() {
  const { currentProjectId, updateNodeData } = useStoryboardStore();

  useEffect(() => {
    if (!currentProjectId) return;

    const supabase = getSupabaseClient();
    if (!supabase) {
      console.warn('[useStoryboardRealtime] Supabase client not available');
      return;
    }

    const channel = supabase
      .channel(`storyboard-tasks-${currentProjectId}`)
      .on(
        'postgres_changes',
        {
          event: 'UPDATE',
          schema: 'public',
          table: 'unified_tasks',
          filter: `metadata->>project_id=eq.${currentProjectId}`,
        },
        (payload) => {
          const task = payload.new as {
            id: string;
            status: string;
            progress?: number;
            result?: Record<string, unknown>;
            metadata?: Record<string, unknown>;
          };

          const nodeId = task.metadata?.node_id as string | undefined;
          if (!nodeId) return;

          updateNodeData(nodeId, {
            data_json: {
              task_id: task.id,
              task_status: task.status,
              task_progress: task.progress ?? 0,
              task_result: task.result ?? null,
            },
          });
        }
      )
      .subscribe((status) => {
        if (status === 'SUBSCRIBED') {
          console.debug(`[useStoryboardRealtime] Subscribed to project ${currentProjectId}`);
        }
      });

    return () => {
      supabase.removeChannel(channel);
    };
  }, [currentProjectId, updateNodeData]);
}
