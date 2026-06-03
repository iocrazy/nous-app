// Decide whether the live build (from /version.json) is different from the
// version baked into this running tab. "Different" — not "greater" — because
// any mismatch means the tab is running stale code and should reload to pick
// up the new bundle. Missing/empty latest (fetch failed, dev) → no update, so
// a transient fetch error never nags the user.

export function shouldOfferUpdate(
  current: string | undefined,
  latest: string | null | undefined,
): boolean {
  if (!latest || !current) return false;
  return latest.trim() !== current.trim();
}
