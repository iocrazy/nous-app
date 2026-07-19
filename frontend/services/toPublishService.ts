/**
 * "To Publish" mark — shared plumbing for the Distribution publish picker
 * filter and the Resources context-menu "Mark to publish" action.
 *
 * A single well-known user tag ("To Publish") flags Library assets as queued
 * for publishing. The publish picker's "To publish" filter lists exactly the
 * resources carrying it. The tag is created lazily on first mark.
 */

import {
  addResourceTag, createTag, fetchAllTags, removeResourceTag,
} from './unifiedTagService';
import type { Tag } from '../types';

// Well-known tag name. Matched case-insensitively so a pre-existing tag with
// different casing is reused rather than duplicated.
export const TO_PUBLISH_TAG_NAME = 'To Publish';

// Indigo — kept in sync with the picker's bookmark chip accent.
export const TO_PUBLISH_TAG_COLOR = '#6366f1';

/**
 * Resolve the "To Publish" tag id if it already exists, else null.
 * Case-insensitive match against the global tag list.
 */
export async function findToPublishTagId(): Promise<string | null> {
  const tags = await fetchAllTags();
  const tag = tags.find(
    (t: Tag) => t.name.toLowerCase() === TO_PUBLISH_TAG_NAME.toLowerCase(),
  );
  return tag ? tag.id : null;
}

/**
 * Resolve the "To Publish" tag id, creating the tag on first use.
 */
export async function ensureToPublishTagId(): Promise<string> {
  const existing = await findToPublishTagId();
  if (existing) return existing;
  const created = await createTag({
    name: TO_PUBLISH_TAG_NAME,
    color: TO_PUBLISH_TAG_COLOR,
  });
  return created.id;
}

/**
 * Toggle the "To Publish" mark on a resource.
 *
 * @param resourceId      resource to (un)mark
 * @param currentlyMarked whether the resource is currently marked
 * @returns the new marked state (true = marked, false = unmarked)
 */
export async function toggleToPublish(
  resourceId: string,
  currentlyMarked: boolean,
): Promise<boolean> {
  const tagId = await ensureToPublishTagId();
  if (currentlyMarked) {
    await removeResourceTag(resourceId, tagId);
    return false;
  }
  await addResourceTag(resourceId, tagId);
  return true;
}

/**
 * Whether a resolved tag list carries the "To Publish" mark. Case-insensitive.
 * Accepts the joined `Resource.tags` array shape.
 */
export function hasToPublishTag(tags: Pick<Tag, 'name'>[] | undefined | null): boolean {
  if (!Array.isArray(tags)) return false;
  return tags.some(
    (t) => t.name?.toLowerCase() === TO_PUBLISH_TAG_NAME.toLowerCase(),
  );
}
