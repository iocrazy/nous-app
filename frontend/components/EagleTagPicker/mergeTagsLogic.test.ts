import { describe, it, expect } from 'vitest';
import type { Tag } from '../../types/api';
import { pickDefaultTarget, canMerge } from './mergeTagsLogic';

const tag = (id: string, media_count: number, type = 'user'): Tag =>
  ({ id, name: id, name_zh: null, color: '#fff', type, media_count }) as Tag;

describe('pickDefaultTarget', () => {
  it('returns the highest media_count id', () => {
    expect(pickDefaultTarget([tag('a', 3), tag('b', 12), tag('c', 5)])).toBe('b');
  });
  it('returns null for empty', () => {
    expect(pickDefaultTarget([])).toBeNull();
  });
  it('treats missing media_count as 0', () => {
    const t = ({ id: 'x', name: 'x', name_zh: null, color: '#fff', type: 'user' }) as Tag;
    expect(pickDefaultTarget([t, tag('y', 1)])).toBe('y');
  });
});

describe('canMerge', () => {
  const sel = [tag('a', 3), tag('b', 12)];
  it('true when >=2 user tags and target is one of them', () => {
    expect(canMerge(sel, 'b')).toBe(true);
  });
  it('false with <2 selected', () => {
    expect(canMerge([tag('a', 1)], 'a')).toBe(false);
  });
  it('false when target null', () => {
    expect(canMerge(sel, null)).toBe(false);
  });
  it('false when target not in selection', () => {
    expect(canMerge(sel, 'zzz')).toBe(false);
  });
  // The "not a user tag" case is gone with mig 468: every tag belongs to
  // someone, so refusing on `type` would only ever block tags the user owns.
  // Ownership is enforced where it can actually be trusted — the `merge_tags`
  // proc rejects any tag whose user_id is not the caller's — not in a
  // client-side predicate the browser could simply not run.
  it('true for two of your own tags with the target among them', () => {
    expect(canMerge([tag('a', 3), tag('b', 12)], 'a')).toBe(true);
  });
});
