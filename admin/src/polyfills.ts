// Polyfill crypto.randomUUID for non-secure contexts (HTTP access to NAS).
// Supabase JS internally uses crypto.randomUUID() which is only available
// in secure contexts (HTTPS or localhost). This must be imported before
// any Supabase code.
if (typeof crypto !== 'undefined' && !crypto.randomUUID) {
  crypto.randomUUID = () =>
    '10000000-1000-4000-8000-100000000000'.replace(/[018]/g, (c) =>
      (+c ^ (crypto.getRandomValues(new Uint8Array(1))[0] & (15 >> (+c / 4)))).toString(16),
    ) as `${string}-${string}-${string}-${string}-${string}`
}
