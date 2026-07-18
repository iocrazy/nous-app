import { describe, it, expect } from 'vitest';
import { filterPickerTags } from './index';
import type { Tag } from '../../types';

const t = (id: string, origin?: 'curated' | 'note'): Tag =>
  ({ id, name: id, color: null, icon: null, type: 'user', created_at: '', origin }) as Tag;

describe('filterPickerTags', () => {
  it('hides shadow tags by default', () => {
    expect(filterPickerTags([t('1'), t('2', 'note')], new Set())).toHaveLength(1);
  });
  it('keeps a shadow tag that is already assigned', () => {
    expect(filterPickerTags([t('2', 'note')], new Set(['2']))).toHaveLength(1);
  });
  it('keeps curated and undefined-origin tags', () => {
    expect(filterPickerTags([t('1', 'curated'), t('3')], new Set())).toHaveLength(2);
  });
});
