import { describe, expect, it } from 'vitest';
import {
  FILTER_VALUES,
  SORT_VALUES,
  buildFilterOptions,
  buildSortOptions,
  toggleFilterIn,
  type ResourceFilterType,
} from './resourceFilters';

// Minimal translation stub that returns the key back.
const t: any = (key: string) => key;

describe('buildFilterOptions', () => {
  it('returns one entry per FILTER_VALUES member', () => {
    const options = buildFilterOptions(t);
    expect(options.map(o => o.value)).toEqual([
      'video',
      'image',
      'audio',
      'document',
      'other',
    ]);
    expect(options.map(o => o.value)).toEqual([...FILTER_VALUES]);
  });

  it('labels are the translation keys for smartFolder.fileTypes.*', () => {
    const options = buildFilterOptions(t);
    for (const opt of options) {
      expect(opt.label).toBe(`smartFolder.fileTypes.${opt.value}`);
    }
  });
});

describe('buildSortOptions', () => {
  it('returns all six sort values in order', () => {
    const options = buildSortOptions(t);
    expect(options.map(o => o.value)).toEqual([
      'newest',
      'oldest',
      'name-az',
      'name-za',
      'largest',
      'smallest',
    ]);
    expect(options.map(o => o.value)).toEqual([...SORT_VALUES]);
  });
});

describe('toggleFilterIn', () => {
  it('adds a missing filter', () => {
    const before = new Set<ResourceFilterType>(['video']);
    const after = toggleFilterIn(before, 'image');
    expect([...after].sort()).toEqual(['image', 'video']);
  });

  it('removes an existing filter', () => {
    const before = new Set<ResourceFilterType>(['video', 'image']);
    const after = toggleFilterIn(before, 'video');
    expect([...after]).toEqual(['image']);
  });

  it('does not mutate the input set', () => {
    const before = new Set<ResourceFilterType>(['video']);
    const after = toggleFilterIn(before, 'image');
    expect(before.has('image')).toBe(false);
    expect(after).not.toBe(before);
  });

  it('handles empty input', () => {
    const after = toggleFilterIn(new Set(), 'audio');
    expect([...after]).toEqual(['audio']);
  });
});
