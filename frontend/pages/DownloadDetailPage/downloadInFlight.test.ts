import { describe, it, expect } from 'vitest';
import { isVideoDownloadInFlight, type InFlightTask } from './downloadInFlight';

// Real shapes from production task_tracking rows. task_tracking.media_id holds
// the PLATFORM id — douyin ids are numeric-looking strings, bilibili ones are
// prefixed. Never a parsed_media.id.
const DOUYIN_PLATFORM_ID = '7666276062464475825';
const BILIBILI_PLATFORM_ID = 'bilibili_BV1q3uh6BEAm';
/** A parsed_media.id (Snowflake). Deliberately NOT what tasks are keyed by. */
const PARSED_MEDIA_ID = '324231707128014';

const task = (over: Partial<InFlightTask> = {}): InFlightTask => ({
  task_type: 'download',
  media_id: DOUYIN_PLATFORM_ID,
  ...over,
});

describe('isVideoDownloadInFlight — in-flight state comes from task_tracking', () => {
  // The bug this function exists to kill: 'pending' is the *column default*
  // on parsed_media ("待下载"), set for every media at parse time. Treating it
  // as in-flight left every never-downloaded video spinning forever — 36 rows
  // in prod, oldest from 2026-02-13.
  it('pending with no task → NOT in flight (regression: infinite spinner)', () => {
    expect(isVideoDownloadInFlight([], DOUYIN_PLATFORM_ID, 'pending')).toBe(false);
  });

  it('pending WITH a matching active download task → in flight', () => {
    expect(isVideoDownloadInFlight([task()], DOUYIN_PLATFORM_ID, 'pending')).toBe(true);
  });

  it('matches a non-numeric platform id (bilibili) too', () => {
    expect(
      isVideoDownloadInFlight([task({ media_id: BILIBILI_PLATFORM_ID })], BILIBILI_PLATFORM_ID, 'pending'),
    ).toBe(true);
  });

  // Guards the review finding that shipped in the first cut of this PR: the
  // caller passed video.id (parsed_media.id) while every task row is keyed by
  // platform_id, so the predicate matched 0 of 59 real tasks and the spinner
  // could never appear. Keep this red if anyone re-wires the call site.
  it('a parsed_media.id never matches a task keyed by platform_id', () => {
    expect(isVideoDownloadInFlight([task()], PARSED_MEDIA_ID, 'pending')).toBe(false);
  });

  it("status 'downloading' → in flight even with no task row", () => {
    // Nothing writes this value today, but it is the semantically correct one;
    // honour it so a future backend that does write it needs no frontend change.
    expect(isVideoDownloadInFlight([], DOUYIN_PLATFORM_ID, 'downloading')).toBe(true);
  });

  it('active download task for a DIFFERENT media → not in flight', () => {
    expect(isVideoDownloadInFlight([task({ media_id: '999' })], DOUYIN_PLATFORM_ID, 'pending'))
      .toBe(false);
  });

  it('active task of another type on this media → not in flight', () => {
    expect(isVideoDownloadInFlight([task({ task_type: 'parse' })], DOUYIN_PLATFORM_ID, 'pending'))
      .toBe(false);
  });

  it('terminal statuses are never in flight', () => {
    for (const s of ['completed', 'failed', 'skipped']) {
      expect(isVideoDownloadInFlight([], DOUYIN_PLATFORM_ID, s)).toBe(false);
    }
  });

  it('completed status still reports in-flight while a re-download task runs', () => {
    expect(isVideoDownloadInFlight([task()], DOUYIN_PLATFORM_ID, 'completed')).toBe(true);
  });

  it('missing platform id → not in flight (cannot match a task)', () => {
    expect(isVideoDownloadInFlight([task()], undefined, 'pending')).toBe(false);
  });

  it('a media_id that arrived as a JS number has already lost precision', () => {
    // Documents a trap rather than a supported path. task_tracking.media_id is
    // a TEXT column, so it arrives as a string and this never happens. If some
    // caller did coerce it, ids past 2^53 are already corrupted before we see
    // them (Number('7666276062464475825') → 7666276062464476000) and no
    // String() round-trip can repair that — matching would be wrong, not right.
    const numeric = {
      task_type: 'download',
      media_id: Number(DOUYIN_PLATFORM_ID),
    } as unknown as InFlightTask;
    expect(isVideoDownloadInFlight([numeric], DOUYIN_PLATFORM_ID, 'pending')).toBe(false);
  });

  it('small numeric media_id still matches (coercion is not the problem)', () => {
    const numeric = { task_type: 'download', media_id: 42 } as unknown as InFlightTask;
    expect(isVideoDownloadInFlight([numeric], '42', 'pending')).toBe(true);
  });

  it('undefined status with no task → not in flight', () => {
    expect(isVideoDownloadInFlight([], DOUYIN_PLATFORM_ID, undefined)).toBe(false);
  });
});
