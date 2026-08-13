import type { UnifiedTask } from '../../contexts/TaskManagerContext';

/** The only fields the predicate reads. Narrow on purpose so callers can pass
 * a full UnifiedTask while tests stay cheap to construct. */
export type InFlightTask = Pick<UnifiedTask, 'task_type' | 'media_id'>;

/**
 * Is a video download actually running for this media right now?
 *
 * Sourced from `task_tracking` (via TaskManager's activeTasks), NOT from
 * `parsed_media.video_download_status` — per the repo rule that task_tracking
 * is the single source of truth for UI task state.
 *
 * Why this exists: `video_download_status` defaults to `'pending'` ("待下载")
 * for every media at parse time, and no backend code ever writes
 * `'downloading'` to it — the column only ever moves pending → completed /
 * failed / skipped. Reading `'pending'` as "in flight" therefore made every
 * never-downloaded video render an infinite "Downloading..." spinner while the
 * same screen also said "hasn't been downloaded yet". 36 rows were in that
 * state in production, the oldest parsed 2026-02-13.
 *
 * `'downloading'` is still honoured: nothing writes it today, but it is the
 * semantically correct value, so a backend that starts writing it needs no
 * change here.
 */
export function isVideoDownloadInFlight(
  activeTasks: readonly InFlightTask[],
  mediaId: string | undefined,
  downloadStatus: string | undefined,
): boolean {
  if (downloadStatus === 'downloading') return true;
  // No id to match on → cannot claim a task belongs to this media.
  if (!mediaId) return false;
  // Snowflake ids can arrive as JSON numbers; compare by value, not by type.
  return activeTasks.some(
    (t) => t.task_type === 'download' && String(t.media_id) === String(mediaId),
  );
}
