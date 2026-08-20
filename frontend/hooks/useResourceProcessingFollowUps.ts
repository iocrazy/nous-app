/**
 * useResourceProcessingFollowUps — closes F1's chain, from both ends it can
 * be left hanging.
 *
 * 1. `ensureResourceProcessed` starts a transcription and stops there: the
 *    summary endpoint has nothing to read until the transcript lands, and
 *    the backend says so in as many words ("trigger summary again once
 *    transcript is ready"). This hook watches the Task Center for that
 *    completion and asks for the summary, so the user gets "attach a raw
 *    video, come back to a summarised one" instead of re-triggering by hand.
 * 2. A transcription can also be REFUSED outright: an audio extraction with
 *    no transcription intent holds migration 121's unique slot, so the 200
 *    says "retry once it finishes" and nothing was queued. This hook waits
 *    for that blocker to reach a terminal state and makes the request again
 *    — by then the audio exists, so the retry dispatches for real.
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
import type { EnsureResourceProcessedResult } from '../utils/ensureResourceProcessed';
import { resourceProcessingNotice } from '../utils/resourceProcessingToast';
import {
  forgetPendingAudioRetry,
  forgetTranscriptionFollowUp,
  pendingAudioRetries,
  rememberTranscriptionFollowUp,
  transcriptionFollowUp,
  transcriptionFollowUps,
} from '../utils/transcriptionFollowUp';
import type { TranscriptionFollowUp } from '../utils/transcriptionFollowUp';
import { triggerTranscriptionByResource } from '../services/aiService';

interface TaskLike {
  id?: string | null;
  dbos_workflow_id?: string | null;
  task_type?: string | null;
  resource_id?: string | number | null;
  status?: string | null;
  created_at?: string | null;
  completed_at?: string | null;
  updated_at?: string | null;
  error_msg?: string | null;
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

export interface UseResourceProcessingFollowUpsOptions {
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

/**
 * Is `task` the run this follow-up is waiting on?
 *
 * DISPATCHED entry (`adopted === false`): the run began when we asked, so a
 * task stamped before that instant is a PREVIOUS run for the same resource,
 * and reading its completion would summarise a transcript that the run we
 * actually started is about to overwrite.
 *
 * ADOPTED entry: nothing was dispatched — the trigger deduped onto a run
 * that was already in flight, so `created_at` BEFORE the registration
 * instant is the expected shape, not evidence of staleness. What still has
 * to be excluded is a predecessor that had already finished before we
 * attached, so for this shape the floor moves from "when it started" to
 * "when it ended": a run that reached its terminal state after we attached
 * is the one the backend told us was in progress; one that ended before we
 * attached cannot be. While it is still running there is no terminal stamp
 * and nothing to answer with — but there is also nothing to act on yet, so
 * waiting is correct either way.
 */
function taskAnswersFollowUp(task: TaskLike, entry: TranscriptionFollowUp): boolean {
  const floor = entry.since - CLOCK_SKEW_TOLERANCE_MS;
  const started = new Date(task.created_at ?? 0).getTime();
  if (!Number.isFinite(started) || started >= floor) return true;
  if (!entry.adopted) return false;
  // `completed_at` is written by the DBOS→task_tracking mirror trigger;
  // `updated_at` is the fallback for a row that reached us without it.
  // The fallback only ever ADMITS a task (see `hasTerminalStamp` for the one
  // decision that refuses to rely on it).
  const ended = new Date(task.completed_at ?? task.updated_at ?? 0).getTime();
  return Number.isFinite(ended) && ended > 0 && ended >= floor;
}

/**
 * Did this row REALLY reach a terminal state, or does it only claim to?
 *
 * `completed_at` is stamped by the DBOS→task_tracking mirror trigger when the
 * workflow itself ends, so its presence is the only evidence that the run is
 * actually over. `UnifiedTaskManager.cancel()` is the counter-example that
 * makes this matter: it writes `phase`/`status` DIRECTLY and stamps NO
 * `completed_at`, and it does not stop the DBOS workflow either (it kills
 * registered subprocesses best-effort; cancelling the workflow is still a
 * TODO in that method). A cancelled row can therefore sit in front of a run
 * that is still going and may yet flip to `completed`.
 *
 * Which is why `updated_at` must not stand in here. It moves on ANY write,
 * including that status-only flip, so a row whose transcription is still in
 * flight would date-match the wait and be read as "this is over" — dropping a
 * follow-up the user paid for with nothing said. Admitting a task on the
 * fallback is safe (the worst case is asking for a summary that the backend
 * dedupes); FORGETTING one on it is not, so the two decisions read different
 * fields on purpose.
 */
function hasTerminalStamp(task: TaskLike): boolean {
  const ended = new Date(task.completed_at ?? 0).getTime();
  return Number.isFinite(ended) && ended > 0;
}

export function useResourceProcessingFollowUps(
  options: UseResourceProcessingFollowUpsOptions = {},
): void {
  const { notify, t = defaultTranslate } = options;
  // Read through refs: a parent that rebuilds its `t`/`notify` each render
  // must not re-run the effect (re-running it is harmless but pointless,
  // and the deps list is the thing that keeps this hook honest).
  const notifyRef = useRef(notify);
  notifyRef.current = notify;
  const translateRef = useRef(t);
  translateRef.current = t;

  // One place that turns a result into the user-visible sentence, so all
  // three trigger paths in this feature read identically.
  const announceRef = useRef((result: EnsureResourceProcessedResult) => {
    const notice = resourceProcessingNotice(result, translateRef.current);
    if (notice) notifyRef.current?.(notice.message, notice.type);
  });

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
      const entry = transcriptionFollowUp(resourceId);
      if (entry && !taskAnswersFollowUp(latest, entry)) continue;
      if (latest.status === 'completed') {
        firing.current.add(resourceId);
        forgetTranscriptionFollowUp(resourceId);
        void triggerSummaryByResource(resourceId)
          .then((res) => {
            // Phrased by the same mapper the entry points use, so "queued"
            // and "already running" read identically everywhere.
            announceRef.current({
              action: 'triggered_summary',
              message: res?.message,
              pointsCharged: res?.points_charged,
              alreadyInProgress: isDedupedResponse(res),
            });
          })
          .catch((err) => {
            console.error('useResourceProcessingFollowUps: summary trigger failed', err);
            announceRef.current({
              action: 'failed',
              attempted: 'summary',
              error: err instanceof Error ? err.message : String(err),
            });
          });
      } else if (DEAD_STATUSES.has(String(latest.status)) && hasTerminalStamp(latest)) {
        // No transcript is coming — stop waiting rather than holding the
        // entry forever and re-checking on every task update.
        //
        // Only on a row that carries the mirror trigger's `completed_at`.
        // A dead status with no terminal stamp is the shape `cancel()`
        // writes, and the run behind it may still finish; keeping the entry
        // costs a re-check per task update, dropping it costs the user a
        // summary they asked for and never hear about again.
        forgetTranscriptionFollowUp(resourceId);
      }
    }
  }, [tasks]);

  // ── 2. transcription refused because an audio extraction holds the slot ──
  useEffect(() => {
    for (const entry of pendingAudioRetries()) {
      const key = `audio:${entry.resourceId}`;
      if (firing.current.has(key)) continue;

      if (entry.blockingTaskId) {
        // Needs the Task Center to observe the blocker; without a provider
        // we keep waiting rather than retrying blind (a retry costs points
        // if it lands while the slot is free).
        if (!tasks) continue;
        const blocking = tasks.find(
          (t) => String(t.id ?? '') === entry.blockingTaskId
            || String(t.dbos_workflow_id ?? '') === entry.blockingTaskId,
        );
        if (!blocking) continue;
        if (DEAD_STATUSES.has(String(blocking.status))) {
          // The extraction produced no audio, so a transcription has nothing
          // to work from. Report instead of spending the user's points on a
          // retry whose input never arrived.
          //
          // NOTE: the T1b contract carves this out for `failed` only. We
          // treat `cancelled` / `lost` the same way for the same stated
          // reason — no audio was produced — rather than auto-charging on a
          // path whose outcome we could not observe.
          forgetPendingAudioRetry(entry.resourceId);
          announceRef.current(
            {
              action: 'failed',
              attempted: 'transcribe',
              error: String(blocking.error_msg ?? blocking.status ?? 'audio extraction failed'),
            },
          );
          continue;
        }
        if (String(blocking.status) !== 'completed') continue;
      }
      // blockingTaskId === null: the blocker had already finished when the
      // response was written, so there is nothing to observe — retry now.

      firing.current.add(key);
      forgetPendingAudioRetry(entry.resourceId);
      void triggerTranscriptionByResource(entry.resourceId)
        .then((res) => {
          if (res?.transcription_pending_audio) {
            // Still blocked. Do NOT re-arm: another wait on another blocker
            // is how an automatic retry turns into an unbounded loop. Tell
            // the user, who can click again.
            announceRef.current({ action: 'pending_audio', pointsCharged: 0 });
            return;
          }
          rememberTranscriptionFollowUp(entry.resourceId, {
            adopted: isDedupedResponse(res),
          });
          announceRef.current({
            action: 'triggered_transcribe',
            message: res?.message,
            pointsCharged: res?.points_charged,
            alreadyInProgress: isDedupedResponse(res),
          });
        })
        .catch((err) => {
          console.error('useResourceProcessingFollowUps: transcribe retry failed', err);
          announceRef.current({
            action: 'failed',
            attempted: 'transcribe',
            error: err instanceof Error ? err.message : String(err),
          });
        });
    }
  }, [tasks]);
}
