// frontend/utils/shareMediaUrls.ts
//
// File URLs for the public share page. The visitor has no session, so every
// URL carries the share grant (`access_token` from `POST /shares/code/{code}`)
// as `?share_token=`. The page used to pass the bare share code, which the
// album routes never accepted and which no longer opens a password-protected
// share; the download link carried no credential at all.

import type { ShareVisitorView } from '../types/api';

export interface ShareMediaUrls {
  /** `/api/v1/resources/{id}/file` — the download link and generic fallback. */
  resourceUrl: string | null;
  /** The playable file: `/media/{media_id}`, else the resource file. */
  mediaUrl: string | null;
  coverUrl: string | null;
}

type ShareFileFields = Pick<
  ShareVisitorView,
  'access_token' | 'resource_id' | 'media_id' | 'thumbnail_path'
>;

export function shareTokenQuery(shareToken: string): string {
  return `share_token=${encodeURIComponent(shareToken)}`;
}

export function buildShareMediaUrls(apiBase: string, share: ShareFileFields): ShareMediaUrls {
  const q = shareTokenQuery(share.access_token);
  const resourceId = share.resource_id;
  const mediaId = share.media_id || null;
  const resourceUrl = resourceId ? `${apiBase}/api/v1/resources/${resourceId}/file?${q}` : null;
  const mediaUrl = mediaId ? `${apiBase}/media/${mediaId}?${q}` : resourceUrl;
  let coverUrl: string | null = null;
  if (mediaId) coverUrl = `${apiBase}/media/${mediaId}/cover?${q}`;
  else if (share.thumbnail_path && resourceId) coverUrl = `${apiBase}/media/${resourceId}/cover?${q}`;
  return { resourceUrl, mediaUrl, coverUrl };
}
