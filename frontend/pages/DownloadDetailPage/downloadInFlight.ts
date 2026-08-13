import type { UnifiedTask } from '../../contexts/TaskManagerContext';

/** The only fields the predicate reads. Narrow on purpose so callers can pass
 * a full UnifiedTask while tests stay cheap to construct. */
export type InFlightTask = Pick<UnifiedTask, 'task_type' | 'media_id'>;

/**
 * Is a download actually running for this media right now?
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
 * ⚠️ TRAP: `task_tracking.media_id` holds the **platform_id**, not
 * `parsed_media.id`. Producers write it that way (`media_fetch_helpers.py`
 * `media_id=platform_id`, `workflows/parse.py` `media_id=str(platform_id)`),
 * and `useParser.ts` corroborates by feeding `task.media_id` straight into
 * `fetchVideoByPlatformId`. Measured on the live DB: of 59 download tasks,
 * 0 matched a `parsed_media.id` and 59 matched a `platform_id`. Passing
 * `video.id` here silently matches nothing — the spinner would then never
 * appear, re-creating the same contradiction in the opposite direction.
 *
 * `'downloading'` is still honoured: nothing writes it today, but it is the
 * semantically correct value, so a backend that starts writing it needs no
 * change here.
 *
 * Known imprecision: `dedup_and_dispatch` creates ONE `task_type='download'`
 * row whether the user asked for video, cover, or both, and neither
 * `metadata` (empty `{}` in prod) nor `subtitle` (just the title) records
 * which. A cover-only fetch therefore also reads as "in flight" here. Fixing
 * that needs the producer to record the requested kinds first.
 */
export function isVideoDownloadInFlight(
  activeTasks: readonly InFlightTask[],
  /** parsed_media.platform_id — NOT parsed_media.id. See the TRAP note above. */
  platformId: string | undefined,
  downloadStatus: string | undefined,
): boolean {
  if (downloadStatus === 'downloading') return true;
  // No id to match on → cannot claim a task belongs to this media.
  if (!platformId) return false;
  // String() is defensive only: task_tracking.media_id is a TEXT column, so it
  // arrives as a string. It cannot rescue an id that was already coerced to a
  // JS number upstream — past 2^53 the digits are gone before we see them
  // (the repo-wide Snowflake precision trap), and such a value correctly
  // fails to match rather than matching the wrong media.
  return activeTasks.some(
    (t) => t.task_type === 'download' && String(t.media_id) === String(platformId),
  );
}
