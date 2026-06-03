import { describe, it, expect, vi } from 'vitest';
import { paginateAll } from './paginate';

describe('paginateAll', () => {
  it('drains all pages until a short page', async () => {
    const data = Array.from({ length: 217 }, (_, i) => i);
    const fetchPage = vi.fn(async (offset: number, pageSize: number) =>
      data.slice(offset, offset + pageSize),
    );
    const { items, capped } = await paginateAll(fetchPage, 100, 5000);
    expect(items).toEqual(data);
    expect(capped).toBe(false);
    // 100, 100, 17 → 3 calls (last page is short, stops).
    expect(fetchPage).toHaveBeenCalledTimes(3);
  });

  it('stops on an exactly-full final page followed by an empty page', async () => {
    const data = Array.from({ length: 200 }, (_, i) => i);
    const fetchPage = vi.fn(async (offset: number, pageSize: number) =>
      data.slice(offset, offset + pageSize),
    );
    const { items, capped } = await paginateAll(fetchPage, 100, 5000);
    expect(items).toEqual(data);
    expect(capped).toBe(false);
    // 100, 100, then empty page (length 0 < 100) stops → 3 calls.
    expect(fetchPage).toHaveBeenCalledTimes(3);
  });

  it('flags capped when maxItems is reached', async () => {
    const fetchPage = vi.fn(async (_offset: number, pageSize: number) =>
      Array.from({ length: pageSize }, () => 1),
    );
    const { items, capped } = await paginateAll(fetchPage, 100, 250);
    expect(capped).toBe(true);
    expect(items.length).toBe(300); // 100 + 100 + 100, loop guard is offset<250
    expect(fetchPage).toHaveBeenCalledTimes(3);
  });

  it('returns empty for an immediately empty source', async () => {
    const fetchPage = vi.fn(async () => []);
    const { items, capped } = await paginateAll(fetchPage, 100, 5000);
    expect(items).toEqual([]);
    expect(capped).toBe(false);
    expect(fetchPage).toHaveBeenCalledTimes(1);
  });
});
