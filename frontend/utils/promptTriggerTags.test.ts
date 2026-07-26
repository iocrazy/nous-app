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
  it('returns first prompt_trigger tag', () => {
    const tags = [tag({ id: 'a' }), tag({ id: 'b', prompt_trigger: true }), tag({ id: 'c', prompt_trigger: true })];
    expect(pickDefaultTriggerTag(tags)?.id).toBe('b');
  });
  it('null when none', () => {
    expect(pickDefaultTriggerTag([tag({})])).toBeNull();
  });
});
