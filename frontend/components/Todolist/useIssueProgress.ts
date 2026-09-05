/**
 * `issue.rollup` for one issue: fetched on mount, re-fetched on a cadence that
 * follows the phase (fast while something is running or waiting, slow when
 * idle), and nudged by Realtime on `agent_runs` for the issue's session so a
 * step boundary shows up without waiting for the next tick. The rollup is
 * computed server-side from the runs (never from execution_state alone).
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { getIssueProgress, type IssueProgress } from '../../services/issuesService';
import { getSupabaseClient } from '../../supabaseClient';

export const LIVE_POLL_MS = 4_000;
export const IDLE_POLL_MS = 30_000;

const LIVE_PHASES = new Set(['running', 'waiting_input', 'paused']);

export interface UseIssueProgressResult {
  progress: IssueProgress | null;
  loaded: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

export function useIssueProgress(
  issueId: number | null | undefined,
  sessionId?: string | number | null,
): UseIssueProgressResult {
  const [progress, setProgress] = useState<IssueProgress | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const alive = useRef(true);

  const refresh = useCallback(async () => {
    if (!issueId) return;
    try {
      const next = await getIssueProgress(issueId);
      if (!alive.current) return;
      setProgress(next);
      setError(null);
    } catch (err) {
      if (!alive.current) return;
      console.error('[useIssueProgress] load failed', err);
      setError(err instanceof Error ? err.message : 'progress unavailable');
    } finally {
      if (alive.current) setLoaded(true);
    }
  }, [issueId]);

  useEffect(() => {
    alive.current = true;
    setProgress(null);
    setLoaded(false);
    void refresh();
    return () => {
      alive.current = false;
    };
  }, [refresh]);

  const live = progress ? LIVE_PHASES.has(progress.phase) : true;
  useEffect(() => {
    if (!issueId) return;
    const timer = window.setInterval(() => void refresh(), live ? LIVE_POLL_MS : IDLE_POLL_MS);
    return () => window.clearInterval(timer);
  }, [issueId, live, refresh]);

  useEffect(() => {
    if (!issueId || !sessionId) return;
    const supa = getSupabaseClient();
    if (!supa) return;
    const channel = supa
      .channel(`issue-progress-${issueId}`)
      .on(
        'postgres_changes',
        { event: '*', schema: 'public', table: 'agent_runs', filter: `conversation_id=eq.${sessionId}` },
        () => void refresh(),
      )
      .subscribe();
    return () => {
      void supa.removeChannel(channel);
    };
  }, [issueId, sessionId, refresh]);

  return { progress, loaded, error, refresh };
}
