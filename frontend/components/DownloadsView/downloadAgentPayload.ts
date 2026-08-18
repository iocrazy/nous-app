/**
 * My Downloads holds `parsed_media` rows; every AI trigger and the chat chip
 * are keyed by `resources.id`. This is that translation, pulled out of the
 * context-menu handler so both halves of it are testable: the mapping, and
 * the case where there is nothing to map to.
 *
 * `resources.media_id -> parsed_media.id` is the link. It is loaded per page
 * by `useResourceDataMap`, so a miss means either "this media has no
 * resource row" (rare: 1242/1242 have one as of 2026-08-18) or "the map has
 * not landed yet". Both are indistinguishable from here and both mean the
 * same thing to the user — there is nothing an agent could be handed — so
 * they get one honest answer instead of a silent no-op.
 */

import type { AgentSendableResource } from '../../utils/sendResourceToAgent';
import type { ResourceData } from './useDownloadsData';

export type DownloadAgentPayload =
  | { ok: true; resource: AgentSendableResource }
  | { ok: false; reason: 'no_resource' };

/** Just the fields of a downloads row this needs; keeps the test fixture
 *  from having to fake a whole `Video`. */
export interface DownloadRowLike {
  id?: string | number | null;
  title?: string | null;
  platform_id?: string | null;
}

export function buildDownloadAgentPayload(
  video: DownloadRowLike,
  data: ResourceData | undefined,
): DownloadAgentPayload {
  if (!data?.id) return { ok: false, reason: 'no_resource' };
  return {
    ok: true,
    resource: {
      id: String(data.id),
      // Prefer the resource's own filename so the chip reads the same as it
      // would coming from the @ picker; the card title is the fallback.
      filename: data.filename || video.title || video.platform_id || '',
      // The only reliable kind signal here: downloads store aweme-type
      // numerals ('0', '68', …) in `file_type`, so it cannot tell a video
      // from an image.
      mime_type: data.mime_type ?? null,
      // Downloads always have a backing parsed_media row, and that is where
      // their cover lives — the signal `serve_resource_cover` resolves
      // against.
      media_id: video.id ?? null,
      transcript_status: data.transcript_status ?? null,
      summary_status: data.summary_status ?? null,
    },
  };
}
