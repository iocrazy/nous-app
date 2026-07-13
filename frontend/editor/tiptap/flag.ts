/**
 * `VITE_FEATURE_SCRIPT_TIPTAP` — flag-dark gate for the TipTap editing
 * surface (spec D7). Deliberately a FUNCTION, not a frozen module-level
 * `const` (contrast `COLLAB_ENABLED` in `EditorShell.tsx`, which IS a
 * module-level const): `SceneBlock` is exercised by tests that need to flip
 * this flag per-test via `vi.stubEnv`, and a module-level const would freeze
 * at whatever value was read on first import, only escapable with a
 * dynamic-`import()` + `vi.resetModules()` dance per test. Reading fresh on
 * every call costs nothing (one string compare) and lets ordinary
 * `vi.stubEnv('VITE_FEATURE_SCRIPT_TIPTAP', 'true')` + a normal render just
 * work.
 */
export function isTiptapEnabled(): boolean {
  return import.meta.env.VITE_FEATURE_SCRIPT_TIPTAP === 'true';
}
