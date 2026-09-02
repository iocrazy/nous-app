// features/canvas-core/smart/mediaUrl.ts
// Generated-media URLs are RELATIVE (/api/v1/generated-media/…) — correct
// for apiFetch, wrong as a bare <img src>: the app is served from the
// Pages origin while the API lives on VITE_API_URL, so a relative src 404s
// against the frontend host (2026-08-18 broken-thumbnail incident). Every
// canvas <img>/<video> src must go through this helper.
//
// TWO TIERS (canvas fluency W1/W2). `GET /api/v1/generated-media/{id}/cover`
// used to answer with the original bytes; it now answers with a 1024px WebP
// preview and only returns the original for `?full=1`.
//
//   mediaSrc     — preview tier. Everything painted on the canvas: node
//                  thumbnails, prompt chips, mention grids, compare
//                  thumbnails. Nodes stay here at EVERY zoom level; there is
//                  deliberately no level-of-detail switch, because a canvas
//                  holding dozens of outputs must never pull dozens of
//                  originals.
//   fullResSrc   — original tier, absolute. The places a user opens on
//                  purpose and inspects real pixels: the lightbox, the
//                  compare slider, the unified image editor, the brush
//                  canvas, plus anything that reads bytes back out (bake,
//                  stitch, download).
//   fullResPath  — same, but relative-preserving, for `apiFetch`, which
//                  builds its own base and would lose auth + the
//                  dual-channel failover if handed an absolute url.
//
// `v=2` is a cache bust, not a feature flag. Browsers are still holding the
// OLD `/cover` response — the original — under a 7-day immutable cache, so
// preview consumers have to ask a URL the cache has never seen. Both markers
// are stamped at most once, so composing the helpers (a tool re-running
// mediaSrc over a src the editor already resolved) is safe.

import { getApiUrl } from '../../../utils/apiConfig';

/** Cache-bust generation for the `/cover` preview tier. Bump when the
 *  server-side rendering of previews changes in a way viewers must see. */
const COVER_CACHE_BUST = 'v=2';

/** Only the generated-media cover endpoint has two tiers. `/file` and
 *  `/stream` are single-tier and must be left byte-identical. */
const COVER_RE = /\/api\/v1\/generated-media\/\d+\/cover(?=$|[?#])/;

function isCover(url: string): boolean {
  return COVER_RE.test(url);
}

/** Append `key=value`, unless some value for `key` is already present.
 *  Idempotence is what lets these helpers compose. */
function withParam(url: string, key: string, value: string): string {
  if (new RegExp(`[?&]${key}=`).test(url)) return url;
  const hashAt = url.indexOf('#');
  const base = hashAt === -1 ? url : url.slice(0, hashAt);
  const hash = hashAt === -1 ? '' : url.slice(hashAt);
  return `${base}${base.includes('?') ? '&' : '?'}${key}=${value}${hash}`;
}

function absolutize(url: string): string {
  return url.startsWith('/api/') ? `${getApiUrl()}${url}` : url;
}

/** Preview-tier url, relative-preserving. */
function previewPath(url: string): string {
  if (!isCover(url)) return url;
  const [key, value] = COVER_CACHE_BUST.split('=');
  return withParam(url, key, value);
}

/** Original-resolution url, relative-preserving — hand this to `apiFetch`. */
export function fullResPath(url: string | null | undefined): string {
  if (!url) return '';
  if (!isCover(url)) return url;
  // `full` is typed as an int server-side: `full=true` is a 422.
  return withParam(previewPath(url), 'full', '1');
}

/** Preview-tier `<img>`/`<video>` src. */
export function mediaSrc(url: string | null | undefined): string {
  if (!url) return '';
  return absolutize(previewPath(url));
}

/** Original-resolution `<img>`/`<video>`/`fetch` src. */
export function fullResSrc(url: string | null | undefined): string {
  if (!url) return '';
  return absolutize(fullResPath(url));
}
