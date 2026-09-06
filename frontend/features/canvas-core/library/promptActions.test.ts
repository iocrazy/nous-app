// features/canvas-core/library/promptActions.test.ts
import { describe, expect, it } from 'vitest';
import { appendPositive, buildApplyAllPatch, groupChips, ratioPreset } from './promptActions';
import type { PromptNodeData } from '../smart/types';
import type { PromptEntry } from '../../../services/promptsService';

const text: PromptNodeData = { body: 'draft @', negative_body: undefined, gen: null } as unknown as PromptNodeData;
const image: PromptNodeData = { body: 'x', gen: { kind: 'image', model: '', ratio: '1:1', count: 1 } } as unknown as PromptNodeData;

describe('appendPositive', () => {
  it('starts an empty body, otherwise adds one newline', () => {
    expect(appendPositive('', 'p')).toBe('p');
    expect(appendPositive('draft @  ', 'p')).toBe('draft @\np');
  });
});

describe('buildApplyAllPatch', () => {
  it('replaces body and negative, keeps a cleared negative box', () => {
    expect(buildApplyAllPatch({ positive: 'p', negative: null, params: null, node: text })).toEqual({ body: 'p', negative_body: '' });
  });
  it('maps a preset ratio onto an image node only', () => {
    const p = buildApplyAllPatch({ positive: 'p', negative: 'n', params: { width: 1920, height: 1080 }, node: image });
    expect(p.gen).toEqual({ kind: 'image', model: '', ratio: '16:9', count: 1 });
    expect(buildApplyAllPatch({ positive: 'p', negative: 'n', params: { width: 1920, height: 1080 }, node: text }).gen).toBeUndefined();
  });
  it('leaves ratio alone when the picture size is not a preset', () => {
    expect(buildApplyAllPatch({ positive: 'p', negative: null, params: { width: 832, height: 1216 }, node: image }).gen).toBeUndefined();
    expect(ratioPreset({ width: 832, height: 1216 })).toBeNull();
    expect(ratioPreset({ width: 1024, height: 1024 })).toBe('1:1');
  });
});

describe('groupChips', () => {
  it('ranks tags by frequency then name and caps the list', () => {
    const mk = (tags: string[]) => ({ tags } as PromptEntry);
    expect(groupChips([mk(['b', 'a']), mk(['a']), mk(['c'])], 2)).toEqual(['a', 'b']);
  });
});
