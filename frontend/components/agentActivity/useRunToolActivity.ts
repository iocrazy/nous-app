/**
 * Fetch one run's tool calls from ``agent_run_transcript_events``.
 *
 * Used by the collaboration timeline, which — unlike the chat panel — has only
 * an ``agent_run_id`` and no in-hand trace. The panel must NOT use this hook:
 * it would render every call a second time (see toolActivity.ts's docstring).
 *
 * A finished run's transcript is immutable, so it is fetched once and cached
 * process-wide; a timeline with twenty runs in it would otherwise refetch on
 * every re-render of the thread. Live runs poll until they settle.
 */

import { useEffect, useMemo, useState } from 'react';

import { aiLibraryService } from '../../services/aiLibraryService';
import type { AgentRunEvent } from '../../types';
import { foldEvents, type TrajectoryNode } from './TrajectoryRenderer/foldEvents';
import {
  denialsFromTranscriptEvents,
  fromTranscriptEvents,
  type CapabilityDenial,
  type ToolActivity,
} from './toolActivity';

const LIVE_POLL_MS = 5_000;
// 500 events per page × 40 = 20k events — far beyond any real run.
const MAX_PAGES = 40;

interface CachedRunToolActivity {
  activities: ToolActivity[];
  denials: CapabilityDenial[];
  events: AgentRunEvent[];
}

/** runId -> {activities, denials}, for runs already known to be finished. */
const settledCache = new Map<string, CachedRunToolActivity>();

/** Exposed for tests — module-level caches otherwise leak between cases. */
export function __clearRunToolActivityCache(): void {
  settledCache.clear();
}

export interface UseRunToolActivityResult {
  activities: ToolActivity[];
  denials: CapabilityDenial[];
  /** The raw transcript, for TrajectoryRenderer (one fetch feeds both views). */
  events: AgentRunEvent[];
  /** Folded trajectory nodes — steps that do not stack (foldEvents). */
  nodes: TrajectoryNode[];
  loaded: boolean;
}

export function useRunToolActivity(
  runId: string | null | undefined,
  isRunning = false,
): UseRunToolActivityResult {
  const [activities, setActivities] = useState<ToolActivity[]>(() =>
    runId ? (settledCache.get(runId)?.activities ?? []) : [],
  );
  const [denials, setDenials] = useState<CapabilityDenial[]>(() =>
    runId ? (settledCache.get(runId)?.denials ?? []) : [],
  );
  const [events, setEvents] = useState<AgentRunEvent[]>(() =>
    runId ? (settledCache.get(runId)?.events ?? []) : [],
  );
  const [loaded, setLoaded] = useState(() =>
    Boolean(runId && settledCache.has(runId)),
  );

  useEffect(() => {
    if (!runId) {
      setActivities([]);
      setDenials([]);
      setEvents([]);
      setLoaded(true);
      return;
    }

    const cached = settledCache.get(runId);
    if (cached && !isRunning) {
      setActivities(cached.activities);
      setDenials(cached.denials);
      setEvents(cached.events);
      setLoaded(true);
      return;
    }

    let cancelled = false;

    const fetchOnce = async (): Promise<void> => {
      try {
        // Always fetch from seq 0 and rebuild: the transcript is small (one
        // run's events) and a full rebuild through fromTranscriptEvents —
        // which dedupes on the DB's UNIQUE(run_id, seq) — cannot double-count
        // the way an append-on-poll accumulator can.
        // …paging with after_seq until the server says there is no more:
        // the endpoint caps a page at `limit` (500) and a truncated
        // transcript would silently fold a truncated prefix (replay makes
        // completeness load-bearing — harness 2b-1 §1).
        const items: AgentRunEvent[] = [];
        let after = 0;
        for (let page = 0; page < MAX_PAGES; page += 1) {
          const resp = await aiLibraryService.getRunEvents(runId, after);
          if (cancelled) return;
          const got = resp.items ?? [];
          items.push(...got);
          if (!resp.has_more || got.length === 0) break;
          after = got[got.length - 1].seq;
        }
        const nextActivities = fromTranscriptEvents(items);
        const nextDenials = denialsFromTranscriptEvents(items);
        if (!isRunning) {
          settledCache.set(runId, { activities: nextActivities, denials: nextDenials, events: items });
        }
        setActivities(nextActivities);
        setDenials(nextDenials);
        setEvents(items);
      } catch (err) {
        // A run owned by another user reads as 404 — the chips just don't
        // render for them. Not an error worth surfacing in the thread.
        if (!cancelled) console.error('[useRunToolActivity] fetch failed:', err);
      } finally {
        if (!cancelled) setLoaded(true);
      }
    };

    void fetchOnce();

    if (!isRunning) return () => {
      cancelled = true;
    };

    const timer = window.setInterval(() => void fetchOnce(), LIVE_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [runId, isRunning]);

  const nodes = useMemo(() => foldEvents(events, { isRunning }), [events, isRunning]);
  return { activities, denials, events, nodes, loaded };
}
