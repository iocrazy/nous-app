import { useEffect, useRef, useState } from 'react';
import { getSupabaseClient } from '../../supabaseClient';
import { useAuth } from '../../hooks/useAuth';
import type { UnifiedTask } from '../../contexts/TaskManagerContext';
import {
  AGENT_RUN_SELECT,
  agentDisplayName,
  agentRunToTask,
  type AgentRunRow,
} from './agentRunPresentation';

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
  // agent_id → display name. Realtime payloads carry the flat agent_runs row
  // with no ai_agents embed, so a live run would otherwise show up without its
  // badge — exactly the row the user is watching. Names come from the initial
  // fetch's embed and, for agents not seen there, one lookup per agent_id.
  const agentNamesRef = useRef<Map<string, string>>(new Map());

  useEffect(() => {
    if (!currentUserId) {
      setTasks([]);
      return;
    }
    const supabase = getSupabaseClient();
    if (!supabase) return;
    let cancelled = false;
    const agentNames = agentNamesRef.current;
    const pendingAgentLookups = new Set<string>();

    const mapRun = (run: AgentRunRow): UnifiedTask =>
      agentRunToTask(run, run.agent_id ? agentNames.get(run.agent_id) : undefined);

    /** Fill in the badge for a realtime row whose agent we haven't named yet. */
    const resolveAgentName = async (agentId: string) => {
      if (agentNames.has(agentId) || pendingAgentLookups.has(agentId)) return;
      pendingAgentLookups.add(agentId);
      const { data, error } = await supabase
        .from('ai_agents')
        .select('slug,name')
        .eq('id', agentId)
        .maybeSingle();
      pendingAgentLookups.delete(agentId);
      if (error) {
        console.error('[AgentRuns] agent name lookup failed:', error);
        return;
      }
      const name = agentDisplayName({ ai_agents: data });
      if (!name || cancelled) return;
      agentNames.set(agentId, name);
      setTasks((prev) =>
        prev.map((t) =>
          t.metadata?.agent_id === agentId && !t.metadata?.agent_name
            ? { ...t, metadata: { ...t.metadata, agent_name: name } }
            : t,
        ),
      );
    };

    const fetchRuns = async () => {
      const { data, error } = await supabase
        .from('agent_runs')
        .select(AGENT_RUN_SELECT)
        .eq('user_id', currentUserId)
        .is('task_id', null)
        .order('created_at', { ascending: false })
        .limit(RECENT_LIMIT);
      if (error) {
        console.error('[AgentRuns] initial fetch failed:', error);
        return;
      }
      if (!cancelled && data) {
        // The embed makes supabase-js's inference give up (no generated DB
        // types in this project), so it lands on GenericStringError[].
        const runs = data as unknown as AgentRunRow[];
        for (const run of runs) {
          const name = agentDisplayName(run);
          if (run.agent_id && name) agentNames.set(run.agent_id, name);
        }
        setTasks(runs.map(mapRun));
      }
    };
    fetchRuns();

    const upsert = (row: AgentRunRow) => {
      // Runs linked to a task_tracking row (mig 282: service workflows like
      // visual analysis / summary) are already represented by their task entry
      // — showing the raw run too duplicated every analysis in the panel
      // (an "L1 analysis of <url>" 🤖 row next to the real task). Hide them;
      // standalone runs (chat / issue turns, task_id NULL) keep showing.
      if (row.task_id) {
        setTasks((prev) => prev.filter((t) => t.id !== String(row.id)));
        return;
      }
      if (row.agent_id && !agentNames.has(row.agent_id)) void resolveAgentName(row.agent_id);
      setTasks((prev) => {
        const mapped = mapRun(row);
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
        // Realtime carries the raw row: a Snowflake id is a number here even
        // when the REST fetch delivered it as a string (see AgentRunRow.id).
        const id = (payload.old as { id?: string | number }).id;
        if (id != null) setTasks((prev) => prev.filter((t) => t.id !== String(id)));
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
