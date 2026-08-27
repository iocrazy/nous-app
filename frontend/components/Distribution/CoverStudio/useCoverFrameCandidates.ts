// components/Distribution/CoverStudio/useCoverFrameCandidates.ts
//
// Evenly spaced preview frames for the stage's filmstrip — the same server
// sampling the old publish-page picker used (`POST /covers/extract` → DBOS →
// `task_tracking.metadata.cover_frames` over Realtime). Ported here when that
// picker was removed: the studio's strip is the one place left that wants it.
//
// Previews are small data URLs (≤14KB each) riding in task metadata; they are
// for seeking with the eye, not for cropping — a click seeks the <video> to
// the frame's timestamp and the real grab/crop re-reads the source there.

import { useCallback, useEffect, useRef, useState } from 'react';

import { getSupabaseClient } from '../../../supabaseClient';
import { extractCoverFrames } from '../../../services/distributionService';
import type { CoverCandidate, CoverFramesMeta } from '../../../types';

const TERMINAL_PHASES: ReadonlySet<string> = new Set(['failed', 'cancelled', 'lost']);
/** Just past the server's 600s deadline — a workflow that dies without
 *  writing metadata must surface as a failure, not a spinner. */
const SAMPLING_TIMEOUT_MS = 11 * 60 * 1000;

export type CandidatesStatus = 'idle' | 'sampling' | 'ready' | 'failed';

export interface CoverFrameCandidates {
  status: CandidatesStatus;
  candidates: CoverCandidate[];
  /** Server sentence when sampling failed with one. */
  error: string | null;
  retry: () => void;
}

export function useCoverFrameCandidates(sourceId: string | null): CoverFrameCandidates {
  const [taskId, setTaskId] = useState<string | null>(null);
  const [meta, setMeta] = useState<CoverFramesMeta | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const runFor = useRef<string | null>(null);

  const start = useCallback(async (id: string) => {
    runFor.current = id;
    setTaskId(null);
    setMeta(null);
    setFailed(null);
    setStarting(true);
    try {
      const { task_id } = await extractCoverFrames({ resource_id: id });
      if (runFor.current === id) setTaskId(task_id);
    } catch (err) {
      console.error('[useCoverFrameCandidates] extract failed:', err);
      if (runFor.current === id) setFailed('extract');
    } finally {
      if (runFor.current === id) setStarting(false);
    }
  }, []);

  useEffect(() => {
    if (!sourceId) {
      runFor.current = null;
      setTaskId(null);
      setMeta(null);
      setFailed(null);
      return;
    }
    void start(sourceId);
  }, [sourceId, start]);

  useEffect(() => {
    if (!taskId) return undefined;
    const supabase = getSupabaseClient();
    if (!supabase || typeof supabase.channel !== 'function') {
      console.error('[useCoverFrameCandidates] no Realtime client — frames cannot stream');
      return undefined;
    }
    const apply = (row: unknown) => {
      const r = row as { metadata?: { cover_frames?: CoverFramesMeta } | null; phase?: string | null } | null;
      const frames = r?.metadata?.cover_frames;
      if (frames) setMeta(frames);
      const phase = r?.phase;
      if (phase && TERMINAL_PHASES.has(phase)) setFailed((f) => f ?? phase);
    };
    const channel = supabase
      .channel(`cover-studio-frames-${taskId}`)
      .on('postgres_changes', {
        event: 'UPDATE', schema: 'public', table: 'task_tracking',
        filter: `dbos_workflow_id=eq.${taskId}`,
      }, (payload) => apply(payload.new))
      .subscribe((chanStatus) => {
        if (chanStatus !== 'SUBSCRIBED') return;
        // Seed: a short video can finish before the socket joins.
        void supabase.from('task_tracking').select('metadata, phase')
          .eq('dbos_workflow_id', taskId).maybeSingle()
          .then(({ data, error }) => {
            if (error) console.error('[useCoverFrameCandidates] seed failed', error);
            else apply(data);
          });
      });
    const watchdog = setTimeout(() => setFailed((f) => f ?? 'timeout'), SAMPLING_TIMEOUT_MS);
    return () => {
      clearTimeout(watchdog);
      supabase.removeChannel(channel);
    };
  }, [taskId]);

  const candidates = meta?.candidates ?? [];
  const serverError = meta?.error ?? null;
  const status: CandidatesStatus = !sourceId
    ? 'idle'
    : candidates.length > 0
      ? 'ready'
      : serverError || failed
        ? 'failed'
        : starting || taskId
          ? 'sampling'
          : 'idle';

  return {
    status,
    candidates,
    error: serverError,
    retry: () => { if (sourceId) void start(sourceId); },
  };
}
