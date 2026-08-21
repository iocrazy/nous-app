/**
 * ensureResourceProcessed — top up whatever AI processing an attached
 * resource is missing, so the agent has something to read this turn.
 *
 * Both entry points (the @ picker and the resource context menu's "Send
 * to Agent") call this before the chip lands in the composer. The agent
 * itself never triggers processing — that is a permission design left out
 * of scope on purpose; the trigger stays on the user action.
 *
 * The backend endpoints dedup in flight (a repeat call answers 200
 * "already in progress" and charges nothing), so this helper does not try
 * to be clever about `pending` / `processing` — a stale status column that
 * claims "processing" for a dead task would otherwise strand the resource
 * forever.
 *
 * They also short-circuit work that has already FINISHED (200
 * `already_transcribed` / `already_summarized`, nothing queued, nothing
 * charged). That arm is the backstop for the row this helper decides from
 * being a stale snapshot — which is exactly how a resource transcribed at
 * 12:29 got billed for a second transcription at 12:31 in production. It
 * is not a failure and not a dedup: the content is there, so the chain
 * moves straight on to whatever step is still missing.
 */

import {
  triggerSummaryByResource,
  triggerTranscriptionByResource,
} from '../services/aiService';
import type { ResourceAITriggerResponse } from '../services/aiService';
import { fetchResourceById } from '../services/resourceService';
import {
  rememberPendingAudioRetry,
  rememberTranscriptionFollowUp,
} from './transcriptionFollowUp';

/** What the helper did. `skipped` = nothing to trigger for this kind of
 *  resource (or the backend already declared the step not applicable). */
export type EnsureResourceProcessedAction =
  | 'triggered_transcribe'
  | 'triggered_summary'
  | 'ready'
  | 'skipped'
  | 'status_unknown'
  | 'pending_audio'
  | 'failed';

export interface EnsureResourceProcessedResult {
  action: EnsureResourceProcessedAction;
  /** Which step was attempted — only set when `action === 'failed'`. */
  attempted?: 'transcribe' | 'summary';
  /** Message for a user-visible failure notice; only set when failed. */
  error?: string;
  /** The backend's own message, verbatim; only set when a trigger ran. */
  message?: string;
  /** Points the call actually charged. Transcribe only — the summary
   *  endpoint never reports a cost. 0 on the dedup path (Task 1b). */
  pointsCharged?: number;
  /** Set on `pending_audio`: the extraction task to wait on, or null when
   *  it had already finished before the response was written. */
  blockingTaskId?: string | null;
  /** True when the 200 came from in-flight dedup rather than a new
   *  dispatch: nothing was queued and nothing was charged. Both trigger
   *  endpoints answer 200 either way, so the caller cannot tell from the
   *  status code — and the difference is the difference between "we are
   *  spending your points" and "we are not". */
  alreadyInProgress?: boolean;
  /** True when the transcribe trigger answered "already transcribed": our
   *  status snapshot was stale, no run was started and no points were
   *  spent, and the result below is about the step AFTER transcription. */
  alreadyTranscribed?: boolean;
  /** Same answer for the summary half: the summary was already there, so
   *  `action` is 'ready' without anything having been started or charged.
   *  Distinct from `alreadyInProgress`, which means a run IS happening. */
  alreadySummarized?: boolean;
}

export interface EnsureResourceProcessedInput {
  id: string;
  /** Search-result kind when known ('video' | 'audio' | 'image' | ...). */
  kind?: string | null;
  /** Falls back to the mime type when the caller has no kind. */
  mime?: string | null;
  /**
   * `none | pending | processing | completed | failed | skipped`.
   *
   * Absent / empty means UNKNOWN, which is NOT the same as `'none'`: some
   * callers hand over a row synthesised for the grid that never carried
   * these columns (Project Assets' canvas adapter is one). Reading unknown
   * as "never processed" asks for a PAID transcription on an
   * already-transcribed asset; the endpoint's finished-work short-circuit
   * now refuses to charge for that, but a lookup here still spares the
   * round trip and keeps the answer honest when the column IS available.
   */
  transcript_status?: string | null;
  summary_status?: string | null;
}

/**
 * Did this 200 actually start anything?
 *
 * Both endpoints now answer their dedup arm with `points_charged: 0`
 * (transcribe since Task 1b, summary since the same conflict-capture fix),
 * while their fresh-dispatch arms carry either the real cost or no field at
 * all — so the numeric check alone separates the two. The message match is
 * kept as a fallback for any arm that forgets the field; it matches a
 * backend-owned English string, so if that wording drifts this degrades to
 * "treat it as a new dispatch" (an over-reported charge in a toast), never
 * to a wrong trigger.
 */
export function isDedupedResponse(res: ResourceAITriggerResponse | undefined): boolean {
  if (!res) return false;
  if (res.points_charged === 0) return true;
  return /already in progress/i.test(res.message ?? '');
}

/** A status we were actually told. Empty string / null / undefined all
 *  mean "nobody said", and must not be answered with a guess. */
function isKnown(status: string | null | undefined): status is string {
  return typeof status === 'string' && status !== '';
}

function isAudioVisual(input: EnsureResourceProcessedInput): boolean {
  if (input.kind === 'video' || input.kind === 'audio') return true;
  const mime = input.mime ?? '';
  return mime.startsWith('video/') || mime.startsWith('audio/');
}

/** Never throws: a failed trigger must not block the chat — the caller
 *  gets a typed failure to surface instead. */
export async function ensureResourceProcessed(
  input: EnsureResourceProcessedInput,
): Promise<EnsureResourceProcessedResult> {
  if (!isAudioVisual(input)) return { action: 'skipped' };

  let transcript = input.transcript_status;
  let summary = input.summary_status;
  // Set when the transcribe call answers "already transcribed": the row we
  // were handed lags the server, the transcript is there to read, and the
  // chain carries on to the summary inside this same call.
  let transcriptAlreadyDone = false;

  // Resolve unknown status before deciding anything that costs money. Only
  // when the step we are about to act on is the unknown one — a known
  // "not transcribed" needs no lookup, we already know what to do.
  if (!isKnown(transcript) || (transcript === 'completed' && !isKnown(summary))) {
    try {
      const row = await fetchResourceById(input.id);
      transcript = row?.transcript_status ?? transcript;
      summary = row?.summary_status ?? summary;
    } catch (err) {
      console.error('ensureResourceProcessed: status lookup failed', err);
      return {
        action: 'status_unknown',
        error: err instanceof Error ? err.message : String(err),
      };
    }
    if (!isKnown(transcript)) return { action: 'status_unknown' };
  }

  // 'skipped' is the backend saying "there is nothing here to process"
  // (e.g. no audio track). Re-triggering would just 409.
  if (transcript === 'skipped') return { action: 'skipped' };

  if (transcript !== 'completed') {
    try {
      const res = await triggerTranscriptionByResource(input.id);
      // A 200 that queued NOTHING: an audio extraction with no transcription
      // intent holds the unique slot. It reports `points_charged: 0` like a
      // dedup does, but calling it "already being processed" would be the
      // exact lie the backend removed — no transcription is coming until
      // the caller retries.
      if (res?.transcription_pending_audio) {
        // Retried by useResourceProcessingFollowUps once the blocker reaches
        // a terminal state — by then the audio exists and the same call
        // dispatches a real transcription.
        rememberPendingAudioRetry(input.id, res.blocking_task_id);
        return {
          action: 'pending_audio',
          message: res.message,
          pointsCharged: 0,
          blockingTaskId: res.blocking_task_id ?? null,
        };
      }
      // Nothing is running and nothing needs to: the transcript already
      // exists. Registering a follow-up watcher here would wait forever for
      // a task nobody started, so the chain continues inline instead — the
      // stale-snapshot case ends with the summary getting triggered rather
      // than a duplicate transcription getting billed.
      if (res?.already_transcribed) {
        transcriptAlreadyDone = true;
        // Our snapshot was demonstrably behind the server on the transcript
        // half, so it may be behind on the summary half too. Look it up only
        // when nobody told us, so the fall-through cannot dead-end in
        // `status_unknown` on an asset we just proved is processed.
        if (!isKnown(summary)) {
          try {
            const row = await fetchResourceById(input.id);
            summary = row?.summary_status ?? summary;
          } catch (err) {
            console.error('ensureResourceProcessed: summary lookup failed', err);
          }
        }
      } else {
        // Only now is there a transcript worth waiting for; the summary half
        // of the chain is picked up by useResourceProcessingFollowUps.
        //
        // Registered on the dedup arm too — the user attached this resource
        // to get it READ, and "somebody already started the transcription"
        // does not make the summary any less needed. The flag tells the
        // watcher that the run it is waiting on started before this instant,
        // which is the difference between finishing the chain and waiting
        // forever.
        const deduped = isDedupedResponse(res);
        rememberTranscriptionFollowUp(input.id, { adopted: deduped });
        return {
          action: 'triggered_transcribe',
          message: res?.message,
          pointsCharged: res?.points_charged,
          alreadyInProgress: deduped,
        };
      }
    } catch (err) {
      console.error('ensureResourceProcessed: transcribe trigger failed', err);
      return {
        action: 'failed',
        attempted: 'transcribe',
        error: err instanceof Error ? err.message : String(err),
      };
    }
  }

  /** Carries the transcribe short-circuit onto whatever the summary step
   *  decides, so the notice can say "already transcribed" instead of
   *  implying this call spent points on one. */
  const done = (
    result: EnsureResourceProcessedResult,
  ): EnsureResourceProcessedResult =>
    transcriptAlreadyDone ? { ...result, alreadyTranscribed: true } : result;

  if (summary === 'skipped') return done({ action: 'skipped' });
  if (summary === 'completed') return done({ action: 'ready' });
  // Reached only when the lookup above filled it in or the caller told us;
  // an unknown summary on a transcribed asset never falls through here.
  if (!isKnown(summary)) return done({ action: 'status_unknown' });

  try {
    const res = await triggerSummaryByResource(input.id);
    // The stale-snapshot answer one step down the ladder: a summary that
    // already exists means the agent can read this asset now — 'ready', not
    // a trigger, and definitely not a charge.
    if (res?.already_summarized) {
      return done({
        action: 'ready',
        message: res.message,
        pointsCharged: 0,
        alreadySummarized: true,
      });
    }
    return done({
      action: 'triggered_summary',
      message: res?.message,
      pointsCharged: res?.points_charged,
      alreadyInProgress: isDedupedResponse(res),
    });
  } catch (err) {
    console.error('ensureResourceProcessed: summary trigger failed', err);
    return done({
      action: 'failed',
      attempted: 'summary',
      error: err instanceof Error ? err.message : String(err),
    });
  }
}
