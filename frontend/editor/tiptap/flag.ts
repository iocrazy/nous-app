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
  // Per-browser dogfood escape hatch (M4 cutover): VITE_ env is baked at
  // build time, so flipping it on Vercel flips EVERYONE. localStorage lets a
  // single browser opt in ('on') or back out ('off', overriding the env) on
  // prod without a deploy. Guarded for non-browser (test/SSR) contexts.
  try {
    const override = localStorage.getItem('editor.tiptap');
    if (override === 'on') return true;
    if (override === 'off') return false;
  } catch {
    /* no localStorage (non-browser) — fall through to the env flag */
  }
  return import.meta.env.VITE_FEATURE_SCRIPT_TIPTAP === 'true';
}
