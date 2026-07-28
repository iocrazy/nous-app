import { describe, expect, it } from 'vitest';
import { mergeAssignedTagsIntoAllTags } from './tagMerge';
import type { Tag } from '../types';

const tag = (id: string, name: string): Tag =>
  ({ id, name, color: '#fff', icon: null, type: 'user' }) as Tag;

describe('mergeAssignedTagsIntoAllTags', () => {
  it('appends an assigned tag missing from prev', () => {
    const prev = [tag('t1', 'Anime')];
    const assigned = [{ tag: tag('t1', 'Anime') }, { tag: tag('t2', 'AI') }];
    const result = mergeAssignedTagsIntoAllTags(prev, assigned);
    expect(result.map((t) => t.id)).toEqual(['t1', 't2']);
  });

  it('returns the same array reference when nothing is missing', () => {
    const prev = [tag('t1', 'Anime')];
    const assigned = [{ tag: tag('t1', 'Anime') }];
    const result = mergeAssignedTagsIntoAllTags(prev, assigned);
    expect(result).toBe(prev);
  });

  it('ignores assignment rows with no tag (null/undefined)', () => {
    const prev: Tag[] = [];
    const assigned = [{ tag: null }, { tag: undefined }];
    const result = mergeAssignedTagsIntoAllTags(prev, assigned);
    expect(result).toBe(prev);
  });

  it('dedups by id across multiple missing entries', () => {
    const prev: Tag[] = [];
    const assigned = [{ tag: tag('t2', 'AI') }, { tag: tag('t2', 'AI') }];
    const result = mergeAssignedTagsIntoAllTags(prev, assigned);
    expect(result).toHaveLength(1);
  });
});
