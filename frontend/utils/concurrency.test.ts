import { describe, it, expect, vi } from 'vitest';
import { runWithConcurrency } from './concurrency';

describe('runWithConcurrency', () => {
  it('never exceeds the concurrency limit (tracks in-flight peak)', async () => {
    let inFlight = 0;
    let peak = 0;
    const items = Array.from({ length: 12 }, (_, i) => i);

    await runWithConcurrency(items, 4, async () => {
      inFlight++;
      peak = Math.max(peak, inFlight);
      await new Promise<void>((r) => setTimeout(r, 5));
      inFlight--;
    });

    expect(peak).toBeLessThanOrEqual(4);
    // sanity: the pool was actually used (more than 1 ran concurrently)
    expect(peak).toBeGreaterThan(1);
  });

  it('returns results in input order regardless of completion order', async () => {
    // items [0,1,2] with delays [30ms, 0ms, 15ms] → complete out of order
    const delays = [30, 0, 15];
    const items = [0, 1, 2];

    const results = await runWithConcurrency(
      items,
      3,
      async (n) => {
        await new Promise<void>((r) => setTimeout(r, delays[n]));
        return n * 10;
      },
    );

    expect(results).toEqual([
      { ok: true, value: 0 },
      { ok: true, value: 10 },
      { ok: true, value: 20 },
    ]);
  });

  it('wraps a throwing worker as {ok:false} without rejecting the outer promise', async () => {
    const err = new Error('boom');
    const results = await runWithConcurrency(
      ['good', 'bad', 'good2'],
      2,
      async (item) => {
        if (item === 'bad') throw err;
        return item;
      },
    );

    expect(results[0]).toEqual({ ok: true, value: 'good' });
    expect(results[1]).toEqual({ ok: false, error: err });
    expect(results[2]).toEqual({ ok: true, value: 'good2' });
  });

  it('schedules no work and returns all-failed when signal is already aborted', async () => {
    const worker = vi.fn(async () => 'value');
    const controller = new AbortController();
    controller.abort();

    const results = await runWithConcurrency([1, 2, 3], 2, worker, {
      signal: controller.signal,
    });

    // Worker must NOT be called at all
    expect(worker).not.toHaveBeenCalled();
    expect(results).toHaveLength(3);
    for (const r of results) {
      expect(r.ok).toBe(false);
    }
  });

  it('returns an empty array for empty input', async () => {
    const worker = vi.fn(async () => 'x');
    const results = await runWithConcurrency([], 4, worker);
    expect(results).toEqual([]);
    expect(worker).not.toHaveBeenCalled();
  });
});
