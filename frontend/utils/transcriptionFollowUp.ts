/**
 * transcriptionFollowUp — the two waiting lists behind F1's "chain whatever
 * is missing".
 *
 * `ensureResourceProcessed` can only ever start ONE step per call: a video
 * with no transcript gets a transcription, and the summary it also needs
 * cannot be asked for until the transcript exists (the backend answers
 * "trigger summary again once transcript is ready"). So the second half of
 * the chain has to wait for a task to finish, which happens minutes later
 * and in a different component.
 *
 * A module-level set rather than component state on purpose: the trigger
 * happens in the resource context menu and the completion is noticed by the
 * chat panel, and neither owns the other. Deliberately NOT persisted — a
 * follow-up that survives a reload would fire a summary for work whose
 * outcome nobody watched.
 */

/** resourceId → epoch ms when the transcription was triggered. The instant
 *  matters: the Task Center may still be holding an OLDER completed
 *  transcription for the same resource, and reading that as "the one we
 *  just started has finished" would fire the summary against a transcript
 *  that is about to be overwritten. */
const waiting = new Map<string, number>();

/** Called by `ensureResourceProcessed` right after it starts a transcription. */
export function rememberTranscriptionFollowUp(resourceId: string): void {
  if (!resourceId) return;
  waiting.set(String(resourceId), Date.now());
}

/** Resource ids still waiting for their transcript to land. */
export function transcriptionFollowUps(): string[] {
  return [...waiting.keys()];
}

/** When the wait for `resourceId` started, or null if it is not waiting. */
export function transcriptionFollowUpSince(resourceId: string): number | null {
  return waiting.get(String(resourceId)) ?? null;
}

/** Drop a follow-up — the summary was requested, or the transcription died. */
export function forgetTranscriptionFollowUp(resourceId: string): void {
  waiting.delete(String(resourceId));
}

/**
 * Second list: resources whose transcription was REFUSED because an audio
 * extraction with no transcription intent holds migration 121's unique slot
 * (`transcription_pending_audio: true`). Nothing was queued and nothing was
 * charged; the request has to be made again once the blocker finishes.
 *
 * `blockingTaskId` is null when the blocker had already finished by the time
 * the response was written — there is nothing left to observe, so that entry
 * is retried straight away instead of waiting for a task that will never
 * change state.
 */
export interface PendingAudioRetry {
  resourceId: string;
  blockingTaskId: string | null;
  since: number;
}

const pendingAudio = new Map<string, PendingAudioRetry>();

export function rememberPendingAudioRetry(
  resourceId: string,
  blockingTaskId: string | null | undefined,
): void {
  if (!resourceId) return;
  pendingAudio.set(String(resourceId), {
    resourceId: String(resourceId),
    blockingTaskId: blockingTaskId ? String(blockingTaskId) : null,
    since: Date.now(),
  });
}

export function pendingAudioRetries(): PendingAudioRetry[] {
  return [...pendingAudio.values()];
}

export function forgetPendingAudioRetry(resourceId: string): void {
  pendingAudio.delete(String(resourceId));
}

/** Tests only: module state outlives a single test otherwise. */
export function resetTranscriptionFollowUps(): void {
  waiting.clear();
  pendingAudio.clear();
}
