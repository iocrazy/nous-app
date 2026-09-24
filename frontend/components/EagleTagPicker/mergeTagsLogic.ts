import type { Tag } from '../../types/api';

/** Default survivor = the most-used tag (highest media_count). */
export function pickDefaultTarget(tags: Tag[]): string | null {
  if (tags.length === 0) return null;
  let best = tags[0];
  for (const t of tags) {
    if ((t.media_count ?? 0) > (best.media_count ?? 0)) best = t;
  }
  return String(best.id);
}

/** Merge is allowed for >=2 tags with a target chosen from them.
 *
 * There used to be a third condition — every tag had to be `type === 'user'` —
 * because the initial tags were one shared set nobody was allowed to touch.
 * Mig 468 gave every user their own copy, so that check now only ever excludes
 * tags the user does own. Ownership is still enforced where it belongs: the
 * `merge_tags` proc refuses any tag whose `user_id` is not the caller's. */
export function canMerge(selected: Tag[], targetId: string | null): boolean {
  if (selected.length < 2 || !targetId) return false;
  return selected.some((t) => String(t.id) === targetId);
}
