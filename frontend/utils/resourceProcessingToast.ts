/**
 * resourceProcessingNotice — turns an `ensureResourceProcessed` result into
 * the sentence the user sees.
 *
 * Shared by both entry points (the @ picker and the context menu's Send to
 * Agent) so the same event does not get two different explanations.
 *
 * Why this exists at all: attaching an unprocessed video silently spends
 * points, and a failed trigger silently leaves the agent with nothing to
 * read. Both are invisible without a notice, which is exactly the
 * "user action → agent trigger paths must report a typed outcome" rule.
 */

import type { EnsureResourceProcessedResult } from './ensureResourceProcessed';

export interface ResourceProcessingNotice {
  message: string;
  type: 'info' | 'error';
}

/** i18next's `t(key, defaultValue, options)` shape, narrowed to what we use. */
type Translate = (
  key: string,
  defaultValue: string,
  options?: Record<string, unknown>,
) => string;

export function resourceProcessingNotice(
  result: EnsureResourceProcessedResult,
  t: Translate,
): ResourceProcessingNotice | null {
  if (result.action === 'ready' || result.action === 'skipped') return null;

  // Doing LESS still has to be visible. This arm means "we could not find
  // out whether this asset needs processing, so we did none" — the safe
  // direction (the unsafe one bills a second transcription), but the user
  // would otherwise attach an unprocessed asset believing it was handled.
  if (result.action === 'status_unknown') {
    return {
      type: 'info',
      message: t(
        'chat.resourceProcessing.statusUnknown',
        'Could not check this asset\'s processing state — sent without new processing',
      ),
    };
  }

  // Nothing was queued and nothing was charged, but this is NOT "already
  // running": the blocking task will not produce a transcript, so the user
  // has to come back. Saying "already being processed" here would leave
  // them waiting forever for work nobody started.
  if (result.action === 'pending_audio') {
    return {
      type: 'info',
      message: t(
        'chat.resourceProcessing.pendingAudio',
        'Audio is still being extracted from this asset — try again once that finishes',
      ),
    };
  }

  if (result.action === 'failed') {
    return {
      type: 'error',
      message: result.error
        ? t(
          'chat.resourceProcessing.failedWithReason',
          'Could not start processing this asset: {{error}}',
          { error: result.error },
        )
        : t('chat.resourceProcessing.failed', 'Could not start processing this asset'),
    };
  }

  // Both trigger arms answer 200 whether they dispatched or deduped, so
  // "already running" is a distinct sentence, not a variant of the queued
  // one — and it must never carry a points figure.
  if (result.alreadyInProgress) {
    return {
      type: 'info',
      message: t(
        'chat.resourceProcessing.alreadyRunning',
        'This asset is already being processed — no extra points spent',
      ),
    };
  }

  if (result.action === 'triggered_summary') {
    // The transcript was already on the server and our row was stale. Say
    // so: "Summarising this asset" alone reads as if the transcription we
    // asked for is what got skipped, and leaves the user wondering whether
    // they just paid for one.
    if (result.alreadyTranscribed) {
      return {
        type: 'info',
        message: t(
          'chat.resourceProcessing.alreadyTranscribedSummarizing',
          'Already transcribed — summarising now, no extra points spent',
        ),
      };
    }
    return {
      type: 'info',
      message: t(
        'chat.resourceProcessing.summarizing',
        'Summarising this asset — the agent can read it shortly',
      ),
    };
  }

  // Transcription is the only priced step, and only when the backend told
  // us what it cost. Absent a figure we describe the work, not a price.
  return {
    type: 'info',
    message: result.pointsCharged
      ? t(
        'chat.resourceProcessing.transcribingWithCost',
        'Transcribing this asset ({{points}} points)',
        { points: result.pointsCharged },
      )
      : t(
        'chat.resourceProcessing.transcribing',
        'Transcribing this asset — the agent can read it shortly',
      ),
  };
}
