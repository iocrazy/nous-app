import { describe, it, expect } from 'vitest';
import { buildTagSuggestions } from './InspirationPage';
import type { Tag } from '../types';

const pool = (name: string, name_zh?: string, origin: 'curated' | 'note' = 'curated'): Tag =>
  ({ id: name, name, name_zh, color: null, icon: null, type: 'user', created_at: '', origin }) as Tag;

describe('buildTagSuggestions', () => {
  it('puts curated pool names first, then note history, deduped', () => {
    const out = buildTagSuggestions(
      [{ tag: 'ai', cnt: 3 }, { tag: 'scratch', cnt: 1 }],
      [pool('ai'), pool('copywriting', '文案')],
    );
    expect(out).toEqual(['ai', 'copywriting', '文案', 'scratch']);
  });
  it('excludes shadow pool tags (they are already note history)', () => {
    const out = buildTagSuggestions([{ tag: 'x', cnt: 1 }], [pool('x', undefined, 'note')]);
    expect(out).toEqual(['x']);
  });
});
