import { describe, expect, it } from 'vitest';
import { filterVisibleChips } from './chipVisibility';
import { CHIP_IDS, type ChipId } from './types';

describe('filterVisibleChips', () => {
  it('returns the list unchanged when no allowlist is provided', () => {
    const pinned: ChipId[] = ['tags', 'rating', 'source', 'social'];
    const result = filterVisibleChips(pinned, undefined);
    expect(result).toEqual(['tags', 'rating', 'source', 'social']);
  });

  it('returns the list unchanged when the allowlist is null', () => {
    const pinned: ChipId[] = ['tags', 'rating'];
    const result = filterVisibleChips(pinned, null);
    expect(result).toEqual(['tags', 'rating']);
  });

  it('keeps chips that are in the allowlist', () => {
    const pinned: ChipId[] = ['tags', 'rating', 'source', 'social'];
    const uploads: ChipId[] = [
      'tags', 'rating', 'type', 'ai_status', 'date_added', 'duration', 'aspect',
    ];
    const result = filterVisibleChips(pinned, uploads);
    expect(result).toEqual(['tags', 'rating']);
  });

  it('drops chips that are not in the allowlist (source + social are the uploads exclusions)', () => {
    const pinned: ChipId[] = ['source', 'social', 'tags'];
    const uploads: ChipId[] = [
      'tags', 'rating', 'type', 'ai_status', 'date_added', 'duration', 'aspect',
    ];
    const result = filterVisibleChips(pinned, uploads);
    expect(result).toEqual(['tags']);
    expect(result).not.toContain('source');
    expect(result).not.toContain('social');
  });

  it('preserves the original order when filtering', () => {
    const pinned: ChipId[] = ['social', 'rating', 'source', 'tags', 'type'];
    const uploads: ChipId[] = ['tags', 'rating', 'type'];
    const result = filterVisibleChips(pinned, uploads);
    expect(result).toEqual(['rating', 'tags', 'type']);
  });

  it('returns an empty array when no input chip matches the allowlist', () => {
    const pinned: ChipId[] = ['source', 'social'];
    const uploads: ChipId[] = ['tags', 'rating'];
    expect(filterVisibleChips(pinned, uploads)).toEqual([]);
  });

  it('returns an empty array when input is empty, regardless of allowlist', () => {
    expect(filterVisibleChips([], undefined)).toEqual([]);
    expect(filterVisibleChips([], ['tags'])).toEqual([]);
  });

  it('does not mutate the input arrays', () => {
    const pinned: ChipId[] = ['source', 'social', 'tags'];
    const uploads: ChipId[] = ['tags', 'rating'];
    const pinnedBefore = [...pinned];
    const uploadsBefore = [...uploads];
    filterVisibleChips(pinned, uploads);
    expect(pinned).toEqual(pinnedBefore);
    expect(uploads).toEqual(uploadsBefore);
  });

  it('handles the full CHIP_IDS set passing through a permissive allowlist (identity filter)', () => {
    const result = filterVisibleChips([...CHIP_IDS], [...CHIP_IDS]);
    expect(result).toEqual([...CHIP_IDS]);
  });

  it('handles a non-reference allowlist (different array instances with same ids)', () => {
    const pinned: ChipId[] = ['tags', 'rating', 'source'];
    // Fresh array — exercises Set semantics, not reference equality.
    const allowed: ChipId[] = ['tags', 'rating'];
    expect(filterVisibleChips(pinned, allowed)).toEqual(['tags', 'rating']);
  });
});
