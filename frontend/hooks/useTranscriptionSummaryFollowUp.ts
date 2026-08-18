/**
 * useTranscriptionSummaryFollowUp — closes F1's chain.
 *
 * `ensureResourceProcessed` starts a transcription and stops there: the
 * summary endpoint has nothing to read until the transcript lands, and the
 * backend says so in as many words ("trigger summary again once transcript
 * is ready"). This hook watches the Task Center for that completion and
 * asks for the summary, so the user gets "attach a raw video, come back to
 * a summarised one" instead of having to re-trigger by hand.
 *
 * Mounted by AIChatPanel — the chat is where the freshly-processed resource
 * is about to be read. Where there is no TaskManagerProvider (fullscreen
 * editor routes, RECON#18) the hook does nothing at all and keeps the
 * follow-up queued rather than concluding anything from silence.
 *
 * The summary it asks for is CHARGED, so this path reports its outcome the
 * same way the two entry points do — a trigger the user pays for and never
 * hears about is the silent no-op the repo's discipline rules out.
 */

import { useEffect, useRef } from 'react';
import { triggerSummaryByResource } from '../services/aiService';
import { useOptionalTaskManager } from './useOptionalTaskManager';
import { isDedupedResponse } from '../utils/ensureResourceProcessed';
import { resourceProcessingNotice } from '../utils/resourceProcessingToast';
import {
  forgetTranscriptionFollowUp,
  transcriptionFollowUps,
  transcriptionFollowUpSince,
} from '../utils/transcriptionFollowUp';

interface TaskLike {
  task_type?: string | null;
  resource_id?: string | number | null;
  status?: string | null;
  created_at?: string | null;
}

const DEAD_STATUSES = new Set(['failed', 'cancelled', 'lost']);

/**
 * How far before the trigger instant a task may be stamped and still count
 * as "ours". `task_tracking.created_at` comes from the SERVER's clock while
 * the waiting list is stamped with the BROWSER's; a strict comparison would
 * strand the chain forever on any machine whose clock runs ahead. Wide
 * enough to absorb ordinary skew, far narrower than the "stale completed
 * transcription from an earlier session" case this guards against.
 */
const CLOCK_SKEW_TOLERANCE_MS = 60_000;

type Translate = (
  key: string,
  defaultValue: string,
  options?: Record<string, unknown>,
) => string;

export interface UseTranscriptionSummaryFollowUpOptions {
  /** Surfaces the outcome; omit and the path stays console-only. */
  notify?: (message: string, type: 'info' | 'error') => void;
  t?: Translate;
}

const defaultTranslate: Translate = (_key, defaultValue, options) =>
  defaultValue.replace(/\{\{(\w+)\}\}/g, (_m, name) => String(options?.[name] ?? ''));

/** Latest task of `type` for this resource — RECON#19's match, including
 *  the String() on both sides (resource_id crosses the wire as a JSON
 *  number on some routers and a string on others; a bare === would just
 *  never match and the chain would silently never complete). */
function latestTaskFor(
  tasks: TaskLike[],
  resourceId: string,
  type: string,
): TaskLike | undefined {
  return tasks
    .filter(
      (t) => String(t.task_type) === type && String(t.resource_id ?? '') === String(resourceId),
    )
    .sort((a, b) => new Date(b.created_at ?? 0).getTime() - new Date(a.created_at ?? 0).getTime())[0];
}

export function useTranscriptionSummaryFollowUp(
  options: UseTranscriptionSummaryFollowUpOptions = {},
): void {
  const { notify, t = defaultTranslate } = options;
  // Read through refs: a parent that rebuilds its `t`/`notify` each render
  // must not re-run the effect (re-running it is harmless but pointless,
  // and the deps list is the thing that keeps this hook honest).
  const notifyRef = useRef(notify);
  notifyRef.current = notify;
  const translateRef = useRef(t);
  translateRef.current = t;

  const taskManager = useOptionalTaskManager();
  const tasks = (taskManager?.tasks ?? null) as TaskLike[] | null;
  // Belt and braces against a re-render racing the async trigger: the
  // waiting-list entry is dropped before the await, and this guards the
  // window where two renders read the list in the same tick.
  const firing = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (!tasks) return;
    for (const resourceId of transcriptionFollowUps()) {
      if (firing.current.has(resourceId)) continue;
      // Only `ai_transcription` counts. A completed `extract_audio` means
      // the transcription is about to start, not that it finished.
      const latest = latestTaskFor(tasks, resourceId, 'ai_transcription');
      if (!latest) continue;
      // Only tasks from this trigger onward may be read as its outcome.
      const since = transcriptionFollowUpSince(resourceId);
      if (since !== null) {
        const stamped = new Date(latest.created_at ?? 0).getTime();
        if (Number.isFinite(stamped) && stamped < since - CLOCK_SKEW_TOLERANCE_MS) continue;
      }
      if (latest.status === 'completed') {
        firing.current.add(resourceId);
        forgetTranscriptionFollowUp(resourceId);
        const announce = (message: string, type: 'info' | 'error') =>
          notifyRef.current?.(message, type);
        void triggerSummaryByResource(resourceId)
          .then((res) => {
            // Phrased by the same mapper the other two entry points use, so
            // "queued" and "already running" read identically everywhere.
            const notice = resourceProcessingNotice(
              {
                action: 'triggered_summary',
                message: res?.message,
                pointsCharged: res?.points_charged,
                alreadyInProgress: isDedupedResponse(res),
              },
              translateRef.current,
            );
            if (notice) announce(notice.message, notice.type);
          })
          .catch((err) => {
            console.error('useTranscriptionSummaryFollowUp: summary trigger failed', err);
            const notice = resourceProcessingNotice(
              {
                action: 'failed',
                attempted: 'summary',
                error: err instanceof Error ? err.message : String(err),
              },
              translateRef.current,
            );
            if (notice) announce(notice.message, notice.type);
          });
      } else if (DEAD_STATUSES.has(String(latest.status))) {
        // No transcript is coming — stop waiting rather than holding the
        // entry forever and re-checking on every task update.
        forgetTranscriptionFollowUp(resourceId);
      }
    }
  }, [tasks]);
}
