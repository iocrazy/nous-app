// frontend/hooks/useAgentRuns.ts
// Subscribes to the Supabase Realtime channel for agent_runs, scoped to the
// authenticated user, and exposes a set of agent_ids that currently have a
// row with status='running'. Consumed by SidebarAgents to render the pulse
// indicator.
//
// Realtime RLS note
// -----------------
// The client-side `.filter('user_id=eq...')` below is a performance hint to
// Realtime, NOT a security boundary. Actual row-level protection depends on
// (a) the RLS policy on agent_runs (shipped in migration 145), and (b) the
// Supabase project having Realtime-RLS enforcement turned on. Both are
// required; verify via Supabase dashboard (Database → Replication) that
// agent_runs is in the supabase_realtime publication AND that Realtime RLS
// is enabled for the project. If either is misconfigured, users could
// subscribe to other users' channels.
//
// This hook additionally filters every incoming payload against the user's
// own id as defense-in-depth — if a mis-scoped row somehow arrives (RLS
// disabled, policy gap), the UI won't leak it.

import { useCallback, useEffect, useRef, useState } from 'react';
import { getSupabaseClient } from '../supabaseClient';

interface AgentRunRow {
  id: string;
  agent_id: string;
  user_id: string;
  status: 'running' | 'completed' | 'failed' | 'cancelled' | 'heartbeat_lost';
}

interface UseAgentRunsReturn {
  /** Set of agent_ids with at least one row in status='running'. */
  runningAgentIds: Set<string>;
  /** True once the initial snapshot fetch has completed. */
  loaded: boolean;
}

export function useAgentRuns(userId: string | null): UseAgentRunsReturn {
  const [runningAgentIds, setRunningAgentIds] = useState<Set<string>>(new Set());
  const [loaded, setLoaded] = useState(false);
  const countsRef = useRef<Map<string, number>>(new Map());

  // Recompute the set from the counts map — called on every event.
  const syncSet = useCallback(() => {
    const next = new Set<string>();
    for (const [agentId, count] of countsRef.current) {
      if (count > 0) next.add(agentId);
    }
    setRunningAgentIds(next);
  }, []);

  const applyRow = useCallback(
    (row: AgentRunRow, event: 'INSERT' | 'UPDATE' | 'DELETE') => {
      // Defense-in-depth: ignore rows not owned by the current user even if
      // Realtime broadcasts them (guards against Realtime-RLS misconfig).
      if (!userId || row.user_id !== userId) return;

      const counts = countsRef.current;
      const prev = counts.get(row.agent_id) ?? 0;

      if (event === 'INSERT') {
        if (row.status === 'running') {
          counts.set(row.agent_id, prev + 1);
          syncSet();
        }
      } else if (event === 'DELETE') {
        if (prev > 0) {
          counts.set(row.agent_id, prev - 1);
          syncSet();
        }
      } else {
        // UPDATE — the status might have flipped from running → terminal
        // (or the reverse for resurrections, which shouldn't happen but we
        // tolerate). We can't see the old status from the payload shape,
        // so treat each UPDATE as a "is it still running?" recompute.
        // If terminal: decrement when we previously counted it.
        if (row.status === 'running' && prev === 0) {
          counts.set(row.agent_id, 1);
          syncSet();
        } else if (row.status !== 'running' && prev > 0) {
          counts.set(row.agent_id, prev - 1);
          syncSet();
        }
      }
    },
    [userId, syncSet],
  );

  // Initial snapshot — fetch all running rows for this user on mount.
  useEffect(() => {
    if (!userId) {
      countsRef.current = new Map();
      setRunningAgentIds(new Set());
      setLoaded(true);
      return;
    }

    let cancelled = false;
    const supabase = getSupabaseClient();

    void (async () => {
      try {
        const { data, error } = await supabase
          .from('agent_runs')
          .select('id,agent_id,user_id,status')
          .eq('user_id', userId)
          .eq('status', 'running');

        if (cancelled) return;
        if (error) {
          console.error('[useAgentRuns] snapshot error:', error);
          setLoaded(true);
          return;
        }

        const counts = new Map<string, number>();
        for (const row of (data ?? []) as AgentRunRow[]) {
          counts.set(row.agent_id, (counts.get(row.agent_id) ?? 0) + 1);
        }
        countsRef.current = counts;
        syncSet();
        setLoaded(true);
      } catch (err) {
        if (cancelled) return;
        console.error('[useAgentRuns] snapshot failed:', err);
        setLoaded(true);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [userId, syncSet]);

  // Realtime subscription — wired separately so snapshot can complete first.
  useEffect(() => {
    if (!userId) return;
    const supabase = getSupabaseClient();
    const channel = supabase
      .channel(`agent-runs-${userId}`)
      .on(
        'postgres_changes',
        {
          event: 'INSERT',
          schema: 'public',
          table: 'agent_runs',
          filter: `user_id=eq.${userId}`,
        },
        (payload) => applyRow(payload.new as AgentRunRow, 'INSERT'),
      )
      .on(
        'postgres_changes',
        {
          event: 'UPDATE',
          schema: 'public',
          table: 'agent_runs',
          filter: `user_id=eq.${userId}`,
        },
        (payload) => applyRow(payload.new as AgentRunRow, 'UPDATE'),
      )
      .on(
        'postgres_changes',
        {
          event: 'DELETE',
          schema: 'public',
          table: 'agent_runs',
          filter: `user_id=eq.${userId}`,
        },
        (payload) => applyRow(payload.old as AgentRunRow, 'DELETE'),
      )
      .subscribe();

    return () => {
      void supabase.removeChannel(channel);
    };
  }, [userId, applyRow]);

  return { runningAgentIds, loaded };
}

export default useAgentRuns;
