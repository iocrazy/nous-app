import type { Tag } from '../../types';

/** Default survivor = the most-used tag (highest media_count). */
export function pickDefaultTarget(tags: Tag[]): string | null {
  if (tags.length === 0) return null;
  let best = tags[0];
  for (const t of tags) {
    if ((t.media_count ?? 0) > (best.media_count ?? 0)) best = t;
  }
  return String(best.id);
}

/** Merge is allowed only for >=2 user tags with a target chosen from them. */
export function canMerge(selected: Tag[], targetId: string | null): boolean {
  if (selected.length < 2 || !targetId) return false;
  if (!selected.some((t) => String(t.id) === targetId)) return false;
  return selected.every((t) => t.type === 'user');
}
