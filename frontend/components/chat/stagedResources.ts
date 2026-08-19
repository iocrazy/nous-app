/**
 * Staged library resources — the composer's holding area for assets picked
 * from the @ menu or sent over from the library context menu.
 *
 * They used to be tiptap nodes sitting inside the sentence. They now live
 * beside the uploaded-file attachments, above the input, which means the
 * composer has two places a resource can come from on send: this array, and
 * whatever inline chips a draft (or a restored session) still carries. The
 * wire shape is unchanged either way — `ResourceRefAttachment` stays the one
 * producer, so the backend cannot tell which path an asset took.
 */

import type { ResourceRefAttachment } from '../../types';
import type { ResourceRefInsertItem } from './ChatInputResourceMention';

/**
 * A resource waiting above the composer. Carries both halves at once: the
 * wire fields (`resource_id` … `scope`) and the snapshot the chip paints
 * from until the Task Center has something fresher to say.
 */
export interface StagedResourceRef {
  resource_id: string;
  name: string;
  kind: string;
  mime: string;
  scope: { type: 'personal' | 'team'; id: string };
  /** Relative cover path (`/api/v1/resources/{id}/cover`) or ''. */
  thumbnail_url: string;
  transcript_status: string;
  summary_status: string;
}

/**
 * Normalise either entry point's item into the staged shape.
 *
 * Absent is normalised to '' rather than left undefined for the same reason
 * the tiptap node defaults its attrs to '': the status helpers treat an
 * empty status as "claims nothing", which is the right silence for the
 * context-menu path that never had those columns.
 */
export function toStagedResource(item: ResourceRefInsertItem): StagedResourceRef {
  return {
    resource_id: String(item.id ?? ''),
    name: item.name ?? '',
    kind: item.kind ?? 'doc',
    mime: item.mime ?? '',
    scope: item.scope ?? { type: 'personal', id: '' },
    thumbnail_url: item.thumbnail_url ?? '',
    transcript_status: item.transcript_status ?? '',
    summary_status: item.summary_status ?? '',
  };
}

/**
 * Append unless the same resource is already waiting. Staging twice is a
 * user double-click, not a request for two copies — and the payload dedups
 * anyway, so a second chip could only ever misreport what will be sent.
 */
export function stageResource(
  list: StagedResourceRef[],
  item: ResourceRefInsertItem,
): StagedResourceRef[] {
  const next = toStagedResource(item);
  if (!next.resource_id) return list;
  if (list.some((s) => s.resource_id === next.resource_id)) return list;
  return [...list, next];
}

/** Drop one staged resource by id (the chip's × button). */
export function removeStagedResource(
  list: StagedResourceRef[],
  resourceId: string,
): StagedResourceRef[] {
  return list.filter((s) => s.resource_id !== resourceId);
}

/** The wire form. Only these five fields cross the boundary. */
export function toRefAttachment(staged: StagedResourceRef): ResourceRefAttachment {
  return {
    kind: 'resource_ref',
    resource_id: staged.resource_id,
    name: staged.name,
    mime: staged.mime,
    scope: staged.scope,
  };
}

/**
 * Union of the two sources, deduped by resource id.
 *
 * Inline chips win a tie only in the sense that they keep their position —
 * the fields are the same snapshot either way. Sending one resource twice
 * would make the backend resolve and bill it twice, so the dedup is not
 * cosmetic.
 */
export function mergeRefAttachments(
  inline: ResourceRefAttachment[],
  staged: StagedResourceRef[],
): ResourceRefAttachment[] {
  const out: ResourceRefAttachment[] = [];
  const seen = new Set<string>();
  for (const ref of [...inline, ...staged.map(toRefAttachment)]) {
    if (seen.has(ref.resource_id)) continue;
    seen.add(ref.resource_id);
    out.push(ref);
  }
  return out;
}
