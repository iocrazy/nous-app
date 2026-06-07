import { describe, it, expect } from 'vitest';
import type { Tag } from '../types';
import { mergeUpdatedTag } from './tagEditMerge';

const baseTag = (overrides: Partial<Tag> = {}): Tag =>
  ({
    id: '1',
    name: 'Prompt',
    name_zh: '提示词',
    color: '#6366f1',
    type: 'user',
    media_count: 12,
    group_id: '99',
    group_name: '创作',
    ...overrides,
  }) as Tag;

const groups = [
  { id: '99', name: '创作' },
  { id: '100', name: '后期' },
];

describe('mergeUpdatedTag', () => {
  it('preserves the previous media_count when the PUT response defaults it to 0', () => {
    const previous = baseTag({ media_count: 12 });
    // PUT /tags/:id returns the bare row: media_count defaults to 0,
    // group_name absent. This is the exact regression — renaming a tag
    // must not zero its chip count.
    const updated = baseTag({
      name: 'Prompt v2',
      media_count: 0,
      group_name: undefined,
    });

    const merged = mergeUpdatedTag(previous, updated, groups);

    expect(merged.media_count).toBe(12);
    expect(merged.name).toBe('Prompt v2');
  });

  it('derives group_name from local groups since the PUT response omits the join', () => {
    const previous = baseTag();
    const updated = baseTag({ group_id: '100', group_name: undefined });

    const merged = mergeUpdatedTag(previous, updated, groups);

    expect(merged.group_name).toBe('后期');
  });

  it('sets group_name to null when the tag has no group', () => {
    const previous = baseTag();
    const updated = baseTag({ group_id: null, group_name: undefined });

    const merged = mergeUpdatedTag(previous, updated, groups);

    expect(merged.group_name).toBeNull();
  });

  it('falls back to the updated count when previous has none', () => {
    const previous = baseTag({ media_count: undefined });
    const updated = baseTag({ media_count: 5 });

    const merged = mergeUpdatedTag(previous, updated, groups);

    expect(merged.media_count).toBe(5);
  });
});
