/**
 * transcriptionFollowUp — the waiting list behind F1's "chain whatever is
 * missing".
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

const waiting = new Set<string>();

/** Called by `ensureResourceProcessed` right after it starts a transcription. */
export function rememberTranscriptionFollowUp(resourceId: string): void {
  if (!resourceId) return;
  waiting.add(String(resourceId));
}

/** Resource ids still waiting for their transcript to land. */
export function transcriptionFollowUps(): string[] {
  return [...waiting];
}

/** Drop a follow-up — the summary was requested, or the transcription died. */
export function forgetTranscriptionFollowUp(resourceId: string): void {
  waiting.delete(String(resourceId));
}

/** Tests only: module state outlives a single test otherwise. */
export function resetTranscriptionFollowUps(): void {
  waiting.clear();
}
