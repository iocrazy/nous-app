/**
 * Composer attachments → the `IssueMessagePost.attachments` wire shape.
 *
 * Its own module, and exported, because the mapping is where an attachment
 * KIND can be lost in silence. Each kind is identified by a different field —
 * a file by `url`, a resource by `resource_id`, an asset by `asset_id` — and a
 * mapper that forgets one does not throw: `AttachmentRequest` has every field
 * Optional, so the request is accepted, the reference resolves to nothing, and
 * the user is told their attachment could not be read. That is exactly what
 * would have happened to `asset_ref` while this lived inline as a two-branch
 * ternary inside `IssueDetailView.handleReply`.
 *
 * Kept next to the view rather than in the service so the service's type stays
 * a description of the wire and this stays a description of the composer.
 */

import type { ComposerAttachment } from './IssueReplyBox';
import type { IssueMessageAttachment } from '../../services/issueMessageService';

/**
 * `undefined` for an empty list, never `[]`.
 *
 * The key is omitted entirely when nothing is attached — an empty array on the
 * wire reads as "an explicit empty list", which is a different statement.
 */
export function toIssueAttachmentPayload(
  attachments: ComposerAttachment[],
): IssueMessageAttachment[] | undefined {
  if (attachments.length === 0) return undefined;
  return attachments.map((a) => {
    if (a.kind === 'resource_ref') {
      return {
        kind: a.kind,
        resource_id: a.resource_id,
        name: a.name,
        mime: a.mime,
        scope: a.scope,
      };
    }
    if (a.kind === 'asset_ref') {
      // `loadout_id` passes through as-is: null is the backend's "use the
      // asset's default loadout", not a missing value to be filled in.
      return {
        kind: a.kind,
        asset_id: a.asset_id,
        loadout_id: a.loadout_id,
        name: a.name,
        mime: a.mime,
        url: a.url,
      };
    }
    if (a.kind === 'output_ref') {
      // A CITATION, not a thing: coordinates only. The trailing branch below
      // would have accepted this kind silently — it reads `url`, which a
      // citation does not have — and posted an attachment whose every
      // coordinate was missing. `version` travels because a citation names one
      // specific version; dropping it would re-point the comment at whatever
      // the object becomes later.
      return {
        kind: a.kind,
        ref_kind: a.ref_kind,
        ref_id: a.ref_id,
        version: a.version,
        title: a.title,
      };
    }
    return { kind: a.kind, url: a.url, mime: a.mime ?? undefined };
  });
}
