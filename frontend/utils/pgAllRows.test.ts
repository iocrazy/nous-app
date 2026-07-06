/**
 * fetchAllRows — the range-loop drain for secondary views that must never
 * silently truncate at PostgREST's 1000-row cap.
 */

import { describe, expect, it, vi } from 'vitest';
import { fetchAllRows, PG_PAGE } from './pgAllRows';

function rows(n: number, offset = 0): number[] {
  return Array.from({ length: n }, (_, i) => offset + i);
}

describe('fetchAllRows', () => {
  it('drains multiple full pages and stops on the first short page', async () => {
    const factory = vi.fn(async (from: number) => {
      if (from === 0) return { data: rows(PG_PAGE, 0), error: null };
      if (from === PG_PAGE) return { data: rows(PG_PAGE, PG_PAGE), error: null };
      return { data: rows(7, PG_PAGE * 2), error: null }; // short page ends it
    });
    const all = await fetchAllRows<number>(factory);
    expect(all).toHaveLength(PG_PAGE * 2 + 7);
    expect(all[0]).toBe(0);
    expect(all[all.length - 1]).toBe(PG_PAGE * 2 + 6);
    expect(factory).toHaveBeenCalledTimes(3);
    // Ranges are the PostgREST inclusive [from, to] convention.
    expect(factory).toHaveBeenNthCalledWith(1, 0, PG_PAGE - 1);
    expect(factory).toHaveBeenNthCalledWith(2, PG_PAGE, PG_PAGE * 2 - 1);
  });

  it('returns a single short page without a second round trip', async () => {
    const factory = vi.fn(async () => ({ data: rows(12), error: null }));
    const all = await fetchAllRows<number>(factory);
    expect(all).toHaveLength(12);
    expect(factory).toHaveBeenCalledTimes(1);
  });

  it('treats an empty first page as an empty result', async () => {
    const factory = vi.fn(async () => ({ data: [], error: null }));
    expect(await fetchAllRows<number>(factory)).toEqual([]);
    expect(factory).toHaveBeenCalledTimes(1);
  });

  it('stops at the maxPages safety ceiling — loudly, never silently', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const factory = vi.fn(async (from: number) => ({
      data: rows(PG_PAGE, from),
      error: null,
    }));
    const all = await fetchAllRows<number>(factory, 3);
    expect(all).toHaveLength(PG_PAGE * 3);
    expect(factory).toHaveBeenCalledTimes(3);
    // The audit's core rule: no silent caps.
    expect(warnSpy).toHaveBeenCalledWith(expect.stringContaining('truncated'));
  });

  it('throws the page error instead of swallowing it', async () => {
    const boom = new Error('PGRST broke');
    const factory = vi.fn(async () => ({ data: null, error: boom }));
    await expect(fetchAllRows(factory)).rejects.toBe(boom);
  });
});
