/**
 * Runs forked from a run (harness 2b-1 §2) and where they branched. A
 * finished run's fork list can still grow (someone forks it later), so it
 * is re-read on mount; a live run polls with the same cadence as its
 * transcript. `forkMarksFor` turns `at_seq` into the folded step node key
 * (`step:<turn>:<step>`) so the trajectory can draw the mark on the boundary.
 */
import { useEffect, useState } from 'react';
import { aiLibraryService } from '../../services/aiLibraryService';
import type { AgentRunEvent } from '../../types';

export interface RunFork {
  run_id: string;
  at_seq: number;
  created_at: string;
  status: string;
}

const LIVE_POLL_MS = 15_000;

export function useRunForks(runId: string | null | undefined, isRunning = false): RunFork[] {
  const [forks, setForks] = useState<RunFork[]>([]);
  useEffect(() => {
    if (!runId) {
      setForks([]);
      return;
    }
    let cancelled = false;
    const fetchOnce = async () => {
      try {
        const resp = await aiLibraryService.getRunForks(runId);
        if (!cancelled) setForks(resp.items ?? []);
      } catch (err) {
        if (!cancelled) console.error('[useRunForks] fetch failed:', err);
      }
    };
    void fetchOnce();
    if (!isRunning) return () => { cancelled = true; };
    const timer = window.setInterval(() => void fetchOnce(), LIVE_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [runId, isRunning]);
  return forks;
}

/** node.key → forked run ids, for the step_start boundaries the forks name. */
export function forkMarksFor(events: AgentRunEvent[], forks: RunFork[]): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  if (forks.length === 0) return out;
  const bySeq = new Map<number, AgentRunEvent>();
  for (const e of events) bySeq.set(e.seq, e);
  for (const f of forks) {
    const ev = bySeq.get(f.at_seq);
    if (!ev || ev.event_type !== 'step_start') continue;
    const turn = ev.payload?.turn;
    const step = ev.payload?.step;
    if (typeof turn !== 'number' || typeof step !== 'number') continue;
    const key = `step:${turn}:${step}`;
    (out[key] ??= []).push(f.run_id);
  }
  return out;
}
