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
/** Per-browser EMERGENCY override ('on'/'off' in localStorage) — null when
 *  unset. Kept strictly as an escape hatch; the OFFICIAL switch is the admin
 *  module registry (system_settings['editor.tiptap_surface'], fetched via
 *  sceneService.fetchTiptapModuleStatus — the project's single toggle
 *  convention). Guarded for non-browser (test/SSR) contexts. */
export function readTiptapOverride(): boolean | null {
  try {
    const override = localStorage.getItem('editor.tiptap');
    if (override === 'on') return true;
    if (override === 'off') return false;
  } catch {
    /* no localStorage (non-browser) — no override */
  }
  return null;
}

/** Resolve the surface: localStorage emergency override → admin DB switch
 *  (when the caller has fetched it) → build-time env (dev fallback only). */
export function isTiptapEnabled(dbEnabled?: boolean | null): boolean {
  const override = readTiptapOverride();
  if (override !== null) return override;
  if (dbEnabled != null) return dbEnabled;
  return import.meta.env.VITE_FEATURE_SCRIPT_TIPTAP === 'true';
}
