/**
 * mergeAssignedTagsIntoAllTags — keep a separately-held `allTags` catalog
 * in sync after a tag-assignment fetch, when that assignment may have
 * created a brand-new tag row (e.g. PromptSection's ensure-trigger-tag
 * on-demand creation, or an AI workflow's write-through tags). Without
 * this, a picker backed by a stale `allTags` snapshot renders the new
 * assignment with no name/color until an unrelated full refetch happens.
 *
 * R1 (spec 2026-07-28-prompt-dataline) — extracted so the three call
 * sites (MediaCard, ResourcesContext, useDownloadsData) that each hold
 * `allTags` locally don't drift out of sync with each other.
 */
import type { Tag } from '../types';

export function mergeAssignedTagsIntoAllTags<T extends { tag?: Tag | null }>(
  prev: Tag[],
  assigned: T[],
): Tag[] {
  const seen = new Set(prev.map((p) => String(p.id)));
  const missing: Tag[] = [];
  for (const it of assigned) {
    const tag = it.tag;
    if (!tag || seen.has(String(tag.id))) continue;
    seen.add(String(tag.id));
    missing.push(tag);
  }
  return missing.length ? [...prev, ...missing] : prev;
}
