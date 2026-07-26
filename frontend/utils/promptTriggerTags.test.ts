import { describe, expect, it, vi } from 'vitest';
import { hasPromptData, pickDefaultTriggerTag } from './promptTriggerTags';
import type { Tag } from '../types';

const tag = (over: Partial<Tag>): Tag =>
  ({ id: '1', name: 't', color: null, icon: null, type: 'user', ...over }) as Tag;

describe('hasPromptData', () => {
  it('true when any of the four fields is non-empty', () => {
    expect(hasPromptData({ gen_prompt: 'x', gen_prompt_zh: null } as never)).toBe(true);
    expect(hasPromptData({ gen_prompt: null, gen_prompt_negative: 'neg' } as never)).toBe(true);
  });
  it('false when all empty/whitespace', () => {
    expect(hasPromptData({ gen_prompt: '  ', gen_prompt_zh: null } as never)).toBe(false);
  });
});

describe('pickDefaultTriggerTag', () => {
  it('returns first prompt_trigger tag by array order when no created_at', () => {
    const tags = [tag({ id: 'a' }), tag({ id: 'b', prompt_trigger: true }), tag({ id: 'c', prompt_trigger: true })];
    expect(pickDefaultTriggerTag(tags)?.id).toBe('b');
  });

  it('returns tag with earliest created_at when present', () => {
    const tags = [
      tag({ id: 'later', prompt_trigger: true, created_at: '2026-07-26T12:00:00Z' }),
      tag({ id: 'earliest', prompt_trigger: true, created_at: '2026-07-26T10:00:00Z' }),
      tag({ id: 'middle', prompt_trigger: true, created_at: '2026-07-26T11:00:00Z' }),
    ];
    expect(pickDefaultTriggerTag(tags)?.id).toBe('earliest');
  });

  it('tags without created_at sort last', () => {
    const tags = [
      tag({ id: 'no_date', prompt_trigger: true }),
      tag({ id: 'has_date', prompt_trigger: true, created_at: '2026-07-26T10:00:00Z' }),
    ];
    expect(pickDefaultTriggerTag(tags)?.id).toBe('has_date');
  });

  it('null when none', () => {
    expect(pickDefaultTriggerTag([tag({})])).toBeNull();
  });
});
