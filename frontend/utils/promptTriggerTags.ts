/**
 * Prompt trigger-tag helpers (spec 2026-07-26-asset-prompt-management).
 * A "trigger tag" (tags.prompt_trigger=true) surfaces the Prompt panel and
 * grid badge on assets that carry it. The default trigger tag is the first
 * prompt_trigger tag in fetchAllTags order; when none exists we create a
 * user-scoped 'AI' tag on demand.
 */
import { createTag } from '../services/unifiedTagService';
import type { Resource, Tag } from '../types';

type PromptFields = Pick<
  Resource,
  'gen_prompt' | 'gen_prompt_zh' | 'gen_prompt_negative' | 'gen_prompt_negative_zh'
>;

export function hasPromptData(r: PromptFields): boolean {
  return [r.gen_prompt, r.gen_prompt_zh, r.gen_prompt_negative, r.gen_prompt_negative_zh]
    .some((v) => !!(v && v.trim()));
}

export function pickDefaultTriggerTag(tags: Tag[]): Tag | null {
  return tags.find((t) => t.prompt_trigger) ?? null;
}

export async function ensureDefaultTriggerTag(allTags: Tag[]): Promise<Tag> {
  const existing = pickDefaultTriggerTag(allTags);
  if (existing) return existing;
  return createTag({
    name: 'AI',
    color: '#6366f1',
    type: 'user',
    prompt_trigger: true,
  } as Parameters<typeof createTag>[0]);
}
