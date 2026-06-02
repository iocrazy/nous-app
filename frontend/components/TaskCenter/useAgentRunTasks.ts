import { useEffect, useRef, useState } from 'react';
import { getSupabaseClient } from '../../supabaseClient';
import { useAuth } from '../../hooks/useAuth';
import type { UnifiedTask } from '../../contexts/TaskManagerContext';
import { agentRunToTask, type AgentRunRow } from './agentRunPresentation';

const SELECT =
  'id,user_id,status,trigger,input_summary,output_summary,error_message,started_at,ended_at,created_at,prompt_tokens,completion_tokens,cost_cents,model';
const RECENT_LIMIT = 50;

/**
 * Live view of the current user's agent runs (chat + issue + scheduled),
 * mapped to the Task Center's UnifiedTask shape. agent_runs is already in the
 * Supabase realtime publication (migration 145), so this stays frontend-only —
 * the agent_runs / task_tracking split is untouched on the backend.
 */
export function useAgentRunTasks(): UnifiedTask[] {
  const { currentUserId } = useAuth();
  const [tasks, setTasks] = useState<UnifiedTask[]>([]);
  const channelRef = useRef<ReturnType<ReturnType<typeof getSupabaseClient>['channel']> | null>(null);

  useEffect(() => {
    if (!currentUserId) {
      setTasks([]);
      return;
    }
    const supabase = getSupabaseClient();
    if (!supabase) return;
    let cancelled = false;

    const fetchRuns = async () => {
      const { data, error } = await supabase
        .from('agent_runs')
        .select(SELECT)
        .eq('user_id', currentUserId)
        .order('created_at', { ascending: false })
        .limit(RECENT_LIMIT);
      if (error) {
        console.error('[AgentRuns] initial fetch failed:', error);
        return;
      }
      if (!cancelled && data) {
        setTasks((data as AgentRunRow[]).map(agentRunToTask));
      }
    };
    fetchRuns();

    const upsert = (row: AgentRunRow) => {
      setTasks((prev) => {
        const mapped = agentRunToTask(row);
        const idx = prev.findIndex((t) => t.id === mapped.id);
        if (idx === -1) return [mapped, ...prev];
        const next = [...prev];
        next[idx] = mapped;
        return next;
      });
    };

    const channel = supabase
      .channel(`user-agent-runs-${currentUserId}`)
      .on('postgres_changes', {
        event: 'INSERT',
        schema: 'public',
        table: 'agent_runs',
        filter: `user_id=eq.${currentUserId}`,
      }, (payload) => upsert(payload.new as AgentRunRow))
      .on('postgres_changes', {
        event: 'UPDATE',
        schema: 'public',
        table: 'agent_runs',
        filter: `user_id=eq.${currentUserId}`,
      }, (payload) => upsert(payload.new as AgentRunRow))
      .on('postgres_changes', {
        event: 'DELETE',
        schema: 'public',
        table: 'agent_runs',
        filter: `user_id=eq.${currentUserId}`,
      }, (payload) => {
        const id = (payload.old as { id?: string }).id;
        if (id) setTasks((prev) => prev.filter((t) => t.id !== id));
      })
      .subscribe();

    channelRef.current = channel;

    return () => {
      cancelled = true;
      if (channelRef.current) {
        supabase.removeChannel(channelRef.current);
        channelRef.current = null;
      }
    };
  }, [currentUserId]);

  return tasks;
}
