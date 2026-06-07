import type { Tag } from '../types';

/**
 * Merge the `PUT /tags/:id` response back into local tag state after an edit.
 *
 * The update endpoint returns the bare `tags` row: it has no `tag_groups`
 * join (so `group_name` is missing) and no recomputed `media_count` (the
 * count is only joined in on the list endpoint, so the response carries
 * `media_count = 0`).
 *
 * Editing a tag's name / color / group never changes which resources are
 * tagged, so:
 * - `group_name` is derived client-side from the local groups list, and
 * - the previous `media_count` is preserved instead of letting the
 *   response's 0 clobber the chip (the same class of bug that once flipped
 *   edited tags into "Uncategorized" — fixed there, missed here).
 *
 * A page refresh would self-heal both via `loadData()`, but the optimistic
 * UI must not regress in the meantime.
 */
export function mergeUpdatedTag(
  previous: Tag,
  updated: Tag,
  groups: ReadonlyArray<{ id: string | number; name: string }>,
): Tag {
  const derivedGroupId = (updated as { group_id?: string | number | null })
    .group_id;
  const derivedGroupName =
    derivedGroupId == null
      ? null
      : groups.find((g) => String(g.id) === String(derivedGroupId))?.name ??
        null;

  return {
    ...updated,
    group_name: derivedGroupName,
    media_count: previous.media_count ?? updated.media_count ?? 0,
  };
}
