// features/canvas-core/smart/mediaUrl.ts
// Generated-media URLs are RELATIVE (/api/v1/generated-media/…) — correct
// for apiFetch, wrong as a bare <img src>: the app is served from the
// Pages origin while the API lives on VITE_API_URL, so a relative src 404s
// against the frontend host (2026-08-18 broken-thumbnail incident). Every
// canvas <img>/<video> src must go through this helper.

import { getApiUrl } from '../../../utils/apiConfig';

export function mediaSrc(url: string | null | undefined): string {
  if (!url) return '';
  if (url.startsWith('/api/')) return `${getApiUrl()}${url}`;
  return url;
}
