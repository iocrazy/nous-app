/**
 * Prompt trigger-tag helpers (spec 2026-07-26-asset-prompt-management).
 * A "trigger tag" (tags.prompt_trigger=true) surfaces the Prompt panel and
 * grid badge on assets that carry it. The default trigger tag is the first
 * prompt_trigger tag in fetchAllTags order; when none exists we create a
 * user-scoped 'AI' tag on demand.
 */
import { createTag, updateTag } from '../services/unifiedTagService';
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
  const promptTriggerTags = tags.filter((t) => t.prompt_trigger);
  if (promptTriggerTags.length === 0) return null;

  // Sort by created_at ascending to get the earliest one (deterministic).
  // Tags may have created_at even if not explicitly typed, so access carefully.
  promptTriggerTags.sort((a, b) => {
    const aTime = a.created_at ? new Date(a.created_at).getTime() : Infinity;
    const bTime = b.created_at ? new Date(b.created_at).getTime() : Infinity;
    return aTime - bTime;  // stable: missing created_at sorts last (Infinity)
  });

  return promptTriggerTags[0];
}

export async function ensureDefaultTriggerTag(allTags: Tag[]): Promise<Tag> {
  const existing = pickDefaultTriggerTag(allTags);
  if (existing) return existing;
  // A user tag named 'AI' may already exist WITHOUT the trigger flag
  // (pre-existing tag, or created before prompt_trigger shipped). Blindly
  // creating hits the (name, type, user_id) unique constraint with a 409 —
  // promote the existing tag instead.
  const named = allTags.find(
    (t) => t.type === 'user' && t.name.trim().toLowerCase() === 'ai',
  );
  if (named) {
    return updateTag(String(named.id), { prompt_trigger: true });
  }
  return createTag({
    name: 'AI',
    color: '#6366f1',
    type: 'user',
    prompt_trigger: true,
  });
}
