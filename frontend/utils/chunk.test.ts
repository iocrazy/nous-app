import { describe, it, expect } from 'vitest';
import { chunked, PG_IN_CHUNK } from './chunk';

describe('chunked', () => {
  it('returns [] for an empty array', () => {
    expect(chunked([], 10)).toEqual([]);
  });

  it('returns one chunk when input fits in a single batch', () => {
    expect(chunked([1, 2, 3], 10)).toEqual([[1, 2, 3]]);
  });

  it('splits into consecutive chunks of at most `size`', () => {
    expect(chunked([1, 2, 3, 4, 5], 2)).toEqual([[1, 2], [3, 4], [5]]);
  });

  it('produces exact full chunks when length is a multiple of size', () => {
    expect(chunked([1, 2, 3, 4], 2)).toEqual([[1, 2], [3, 4]]);
  });

  it('preserves order and covers every element exactly once', () => {
    const input = Array.from({ length: 1007 }, (_, i) => i);
    const chunks = chunked(input, PG_IN_CHUNK);
    expect(chunks.length).toBe(Math.ceil(1007 / PG_IN_CHUNK));
    expect(chunks.flat()).toEqual(input);
    expect(chunks.every((c) => c.length <= PG_IN_CHUNK)).toBe(true);
  });

  it('throws on a non-positive chunk size', () => {
    expect(() => chunked([1], 0)).toThrow(/chunk size/);
    expect(() => chunked([1], -3)).toThrow(/chunk size/);
  });

  it('PG_IN_CHUNK is a sane positive batch size', () => {
    expect(PG_IN_CHUNK).toBeGreaterThan(0);
    expect(PG_IN_CHUNK).toBeLessThanOrEqual(500);
  });
});
