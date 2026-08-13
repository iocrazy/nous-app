import { describe, it, expect } from 'vitest';
import { isVideoDownloadInFlight, type InFlightTask } from './downloadInFlight';

const task = (over: Partial<InFlightTask> = {}): InFlightTask => ({
  task_type: 'download',
  media_id: '324231707128014',
  ...over,
});

describe('isVideoDownloadInFlight — in-flight state comes from task_tracking', () => {
  // The bug this function exists to kill: 'pending' is the *column default*
  // on parsed_media ("待下载"), set for every media at parse time. Treating it
  // as in-flight left every never-downloaded video spinning forever — 36 rows
  // in prod, oldest from 2026-02-13.
  it('pending with no task → NOT in flight (regression: infinite spinner)', () => {
    expect(isVideoDownloadInFlight([], '324231707128014', 'pending')).toBe(false);
  });

  it('pending WITH a matching active download task → in flight', () => {
    expect(isVideoDownloadInFlight([task()], '324231707128014', 'pending')).toBe(true);
  });

  it("status 'downloading' → in flight even with no task row", () => {
    // Nothing writes this value today, but it is the semantically correct one;
    // honour it so a future backend that does write it needs no frontend change.
    expect(isVideoDownloadInFlight([], '324231707128014', 'downloading')).toBe(true);
  });

  it('active download task for a DIFFERENT media → not in flight', () => {
    expect(isVideoDownloadInFlight([task({ media_id: '999' })], '324231707128014', 'pending'))
      .toBe(false);
  });

  it('active task of another type on this media → not in flight', () => {
    expect(isVideoDownloadInFlight([task({ task_type: 'parse' })], '324231707128014', 'pending'))
      .toBe(false);
  });

  it('terminal statuses are never in flight', () => {
    for (const s of ['completed', 'failed', 'skipped']) {
      expect(isVideoDownloadInFlight([], '324231707128014', s)).toBe(false);
    }
  });

  it('completed status still reports in-flight while a re-download task runs', () => {
    expect(isVideoDownloadInFlight([task()], '324231707128014', 'completed')).toBe(true);
  });

  it('missing media id → not in flight (cannot match a task)', () => {
    expect(isVideoDownloadInFlight([task()], undefined, 'pending')).toBe(false);
  });

  it('numeric media_id from the wire still matches the string id', () => {
    // BIGINT ids can arrive as numbers; compare by value, not by type.
    const numeric = { task_type: 'download', media_id: 324231707128014 } as unknown as InFlightTask;
    expect(isVideoDownloadInFlight([numeric], '324231707128014', 'pending')).toBe(true);
  });

  it('undefined status with no task → not in flight', () => {
    expect(isVideoDownloadInFlight([], '324231707128014', undefined)).toBe(false);
  });
});
