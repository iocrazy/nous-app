/**
 * Derive the qishui (Soda) track id for a re-download from a media item.
 * Prefer the id embedded in the original share URL (`track_id` for audio,
 * `ugc_video_id` for UGC video); fall back to `platform_id` (the canonical
 * platform key the backend stored). Returns null when neither is available.
 */
export function sodaTrackId(video: {
  original_url?: string | null;
  platform_id?: string | null;
}): string | null {
  const url = video.original_url;
  if (url) {
    try {
      const qs = new URL(url).searchParams;
      const id = qs.get('track_id') || qs.get('ugc_video_id');
      if (id) return id;
    } catch {
      // not a parseable absolute URL (e.g. short link) — fall through
    }
  }
  return video.platform_id || null;
}
