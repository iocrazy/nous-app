// Frontend feature flags. Convention: VITE_FEATURE_<NAME>, default OFF, read as
// the literal string "true" (Vite inlines import.meta.env at build time, so this
// is statically analyzable and tree-shakeable).

/** Unified conversations API + realtime (Phase 1). OFF → legacy /chat. */
export function conversations(): boolean {
  return import.meta.env.VITE_FEATURE_CONVERSATIONS === 'true';
}
