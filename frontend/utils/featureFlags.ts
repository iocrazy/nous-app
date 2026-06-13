// Island redesign v2 — frontend feature flags. Convention: VITE_FEATURE_<NAME>,
// default OFF, read as the literal string "true" (Vite inlines import.meta.env
// at build time, so this is statically analyzable and tree-shakeable).

/** Island app-shell layout (spec D1–D12). OFF → the classic fixed-sidebar frame. */
export function islandUI(): boolean {
  return import.meta.env.VITE_FEATURE_ISLAND_UI === 'true';
}
