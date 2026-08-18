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
 */

import {
  triggerSummaryByResource,
  triggerTranscriptionByResource,
} from '../services/aiService';
import type { ResourceAITriggerResponse } from '../services/aiService';
import { rememberTranscriptionFollowUp } from './transcriptionFollowUp';

/** What the helper did. `skipped` = nothing to trigger for this kind of
 *  resource (or the backend already declared the step not applicable). */
export type EnsureResourceProcessedAction =
  | 'triggered_transcribe'
  | 'triggered_summary'
  | 'ready'
  | 'skipped'
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
  /** True when the 200 came from in-flight dedup rather than a new
   *  dispatch: nothing was queued and nothing was charged. Both trigger
   *  endpoints answer 200 either way, so the caller cannot tell from the
   *  status code — and the difference is the difference between "we are
   *  spending your points" and "we are not". */
  alreadyInProgress?: boolean;
}

export interface EnsureResourceProcessedInput {
  id: string;
  /** Search-result kind when known ('video' | 'audio' | 'image' | ...). */
  kind?: string | null;
  /** Falls back to the mime type when the caller has no kind. */
  mime?: string | null;
  /** `none | pending | processing | completed | failed | skipped` */
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
function isDedupedResponse(res: ResourceAITriggerResponse | undefined): boolean {
  if (!res) return false;
  if (res.points_charged === 0) return true;
  return /already in progress/i.test(res.message ?? '');
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

  const transcript = input.transcript_status ?? 'none';
  const summary = input.summary_status ?? 'none';

  // 'skipped' is the backend saying "there is nothing here to process"
  // (e.g. no audio track). Re-triggering would just 409.
  if (transcript === 'skipped') return { action: 'skipped' };

  if (transcript !== 'completed') {
    try {
      const res = await triggerTranscriptionByResource(input.id);
      // Only now is there a transcript worth waiting for; the summary half
      // of the chain is picked up by useTranscriptionSummaryFollowUp.
      rememberTranscriptionFollowUp(input.id);
      return {
        action: 'triggered_transcribe',
        message: res?.message,
        pointsCharged: res?.points_charged,
        alreadyInProgress: isDedupedResponse(res),
      };
    } catch (err) {
      console.error('ensureResourceProcessed: transcribe trigger failed', err);
      return {
        action: 'failed',
        attempted: 'transcribe',
        error: err instanceof Error ? err.message : String(err),
      };
    }
  }

  if (summary === 'skipped') return { action: 'skipped' };
  if (summary === 'completed') return { action: 'ready' };

  try {
    const res = await triggerSummaryByResource(input.id);
    return {
      action: 'triggered_summary',
      message: res?.message,
      pointsCharged: res?.points_charged,
      alreadyInProgress: isDedupedResponse(res),
    };
  } catch (err) {
    console.error('ensureResourceProcessed: summary trigger failed', err);
    return {
      action: 'failed',
      attempted: 'summary',
      error: err instanceof Error ? err.message : String(err),
    };
  }
}
