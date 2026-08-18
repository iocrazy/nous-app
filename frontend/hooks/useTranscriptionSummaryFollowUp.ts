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
 */

import { useEffect, useRef } from 'react';
import { triggerSummaryByResource } from '../services/aiService';
import { useOptionalTaskManager } from './useOptionalTaskManager';
import {
  forgetTranscriptionFollowUp,
  transcriptionFollowUps,
} from '../utils/transcriptionFollowUp';

interface TaskLike {
  task_type?: string | null;
  resource_id?: string | number | null;
  status?: string | null;
  created_at?: string | null;
}

const DEAD_STATUSES = new Set(['failed', 'cancelled', 'lost']);

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

export function useTranscriptionSummaryFollowUp(): void {
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
      if (latest.status === 'completed') {
        firing.current.add(resourceId);
        forgetTranscriptionFollowUp(resourceId);
        void triggerSummaryByResource(resourceId).catch((err) => {
          console.error('useTranscriptionSummaryFollowUp: summary trigger failed', err);
        });
      } else if (DEAD_STATUSES.has(String(latest.status))) {
        // No transcript is coming — stop waiting rather than holding the
        // entry forever and re-checking on every task update.
        forgetTranscriptionFollowUp(resourceId);
      }
    }
  }, [tasks]);
}
