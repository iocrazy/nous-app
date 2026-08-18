/**
 * Shared presentation helpers for the two places a library resource shows
 * up in the composer: the @ picker rows and the inline tiptap chip.
 *
 * Both need the same two answers — "what image do I paint?" and "is this
 * thing still being processed?" — and they must agree, because the picker
 * row and the chip it produces are the same resource one click apart.
 */

import { getApiUrl } from '../../utils/apiConfig';

/**
 * `/resources/search` returns `thumbnail_url` as a RELATIVE path
 * (`/api/v1/resources/{id}/cover`) — the base belongs to the frontend.
 * Dropping it straight into `<img src>` would resolve against the Pages
 * origin, whose `_redirects` catch-all answers unknown paths with
 * index.html instead of a 404, so the failure is a silently blank image.
 */
export function resolveResourceThumbnailSrc(path: string | null | undefined): string | null {
  if (!path) return null;
  if (/^(?:[a-z][a-z0-9+.-]*:)?\/\//i.test(path) || path.startsWith('data:')) return path;
  return `${getApiUrl()}${path.startsWith('/') ? '' : '/'}${path}`;
}

/** `processing` = work is in flight; `unprocessed` = nothing has been made
 *  yet and nothing is running; `null` = nothing to show. */
export type ResourceProcessingState = 'processing' | 'unprocessed' | null;

export interface ResourceProcessingInput {
  kind?: string | null;
  mime?: string | null;
  transcriptStatus?: string | null;
  summaryStatus?: string | null;
}

function isAudioVisual(input: ResourceProcessingInput): boolean {
  if (input.kind === 'video' || input.kind === 'audio') return true;
  const mime = input.mime ?? '';
  return mime.startsWith('video/') || mime.startsWith('audio/');
}

function stepState(
  status: string | null | undefined,
): ResourceProcessingState | 'done' | 'skip' | 'unknown' {
  // Absent is NOT the same as 'none'. A caller that never had the status
  // columns (the context-menu path hands over {id, name, kind}) must not
  // make us assert anything: telling a user their fully-transcribed video is
  // "Not processed yet" is a plain lie, and the same conflation one layer up
  // re-runs a *paid* transcription, because the trigger endpoint only dedups
  // in-flight work, not already-finished work.
  if (!status) return 'unknown';
  if (status === 'skipped') return 'skip';
  if (status === 'completed') return 'done';
  if (status === 'pending' || status === 'processing') return 'processing';
  return 'unprocessed';
}

/**
 * Badge state from the status columns alone.
 *
 * The ladder deliberately mirrors `ensureResourceProcessed` (transcript
 * first, then summary, `skipped` means "not applicable"): the badge is the
 * user-visible half of the same decision, and if the two ever disagreed the
 * picker would nag about work the helper refuses to trigger.
 */
export function resourceProcessingState(input: ResourceProcessingInput): ResourceProcessingState {
  if (!isAudioVisual(input)) return null;

  const transcript = stepState(input.transcriptStatus);
  if (transcript === 'skip' || transcript === 'unknown') return null;
  if (transcript !== 'done') return transcript;

  const summary = stepState(input.summaryStatus);
  if (summary === 'skip' || summary === 'done' || summary === 'unknown') return null;
  return summary;
}

/** Task types that mean "this resource's AI processing is moving". */
const PROCESSING_TASK_TYPES = new Set(['ai_transcription', 'ai_summary', 'extract_audio']);

interface TaskLike {
  task_type?: string | null;
  resource_id?: string | number | null;
  status?: string | null;
  created_at?: string | null;
}

/**
 * Refine the insert-time snapshot with whatever the Task Center currently
 * knows. A chip can outlive its snapshot by minutes, and the status columns
 * it was stamped from are only re-read on a fresh search.
 *
 * `tasks == null` is the no-provider case (the floating chat also mounts on
 * the fullscreen editor routes, RECON#18): keep the snapshot rather than
 * pretending the absence of tasks means the absence of work.
 */
export function resolveChipProcessingState(
  snapshot: ResourceProcessingState,
  tasks: TaskLike[] | null | undefined,
  resourceId: string,
): ResourceProcessingState {
  if (!tasks || tasks.length === 0 || !resourceId) return snapshot;

  // Same match as VideoDetailPanel (RECON#19): String() on both sides,
  // because resource_id crosses the wire as a JSON number on some routers
  // and a string on others.
  const latest = tasks
    .filter(
      (t) =>
        PROCESSING_TASK_TYPES.has(String(t.task_type))
        && String(t.resource_id ?? '') === String(resourceId),
    )
    .sort(
      (a, b) => new Date(b.created_at ?? 0).getTime() - new Date(a.created_at ?? 0).getTime(),
    )[0];

  if (!latest) return snapshot;
  if (latest.status === 'completed') return null;
  if (latest.status === 'failed' || latest.status === 'cancelled' || latest.status === 'lost') {
    return 'unprocessed';
  }
  return 'processing';
}
