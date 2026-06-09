import { describe, it, expect, vi } from 'vitest';
import { runBatch } from './batchRunner';

describe('runBatch', () => {
  it('returns empty outcome for empty input without calling fn', async () => {
    const fn = vi.fn();
    const out = await runBatch([], fn);
    expect(out).toEqual({ succeeded: [], failed: [] });
    expect(fn).not.toHaveBeenCalled();
  });

  it('runs every id and reports all successes', async () => {
    const seen: string[] = [];
    const out = await runBatch(['a', 'b', 'c'], async (id) => {
      seen.push(id);
    });
    expect(seen.sort()).toEqual(['a', 'b', 'c']);
    expect(out.succeeded.sort()).toEqual(['a', 'b', 'c']);
    expect(out.failed).toEqual([]);
  });

  it('isolates failures — one rejection does not abort the batch', async () => {
    const out = await runBatch(['ok1', 'bad', 'ok2'], async (id) => {
      if (id === 'bad') throw new Error('boom');
    });
    expect(out.succeeded.sort()).toEqual(['ok1', 'ok2']);
    expect(out.failed).toHaveLength(1);
    expect(out.failed[0].id).toBe('bad');
    expect((out.failed[0].error as Error).message).toBe('boom');
  });

  it('respects the concurrency ceiling', async () => {
    let inFlight = 0;
    let peak = 0;
    const ids = Array.from({ length: 10 }, (_, i) => String(i));
    await runBatch(
      ids,
      async () => {
        inFlight += 1;
        peak = Math.max(peak, inFlight);
        await new Promise((r) => setTimeout(r, 5));
        inFlight -= 1;
      },
      { concurrency: 3 },
    );
    expect(peak).toBeLessThanOrEqual(3);
  });

  it('drives onProgress to total', async () => {
    const progress: Array<[number, number]> = [];
    await runBatch(['a', 'b', 'c'], async () => {}, {
      onProgress: (done, total) => progress.push([done, total]),
    });
    expect(progress).toHaveLength(3);
    expect(progress[progress.length - 1]).toEqual([3, 3]);
  });
});
