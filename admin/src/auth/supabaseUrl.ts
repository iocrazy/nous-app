/**
 * Where the admin talks to Supabase.
 *
 * Production builds leave `VITE_SUPABASE_URL` EMPTY on purpose: the bundle
 * then uses `<its own origin>/sb`, which the image's nginx proxies to kong
 * (same-origin, so the admin works from every address it is served on —
 * `http://127.0.0.1:8090` on gpupc and `http://heygo-ubuntu:8090` over
 * Tailscale alike — without baking any host into the build). A non-empty
 * value (local dev, a hosted Supabase) is used as given.
 */
export const SAME_ORIGIN_SUPABASE_PATH = '/sb'

export function resolveSupabaseUrl(configured: string | undefined, origin: string): string {
  const explicit = (configured ?? '').trim()
  if (explicit) return explicit.replace(/\/+$/, '')
  return `${origin.replace(/\/+$/, '')}${SAME_ORIGIN_SUPABASE_PATH}`
}
