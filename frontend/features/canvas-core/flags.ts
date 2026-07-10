// frontend/features/canvas-core/flags.ts
//
// Single source of truth for canvas feature flags so the sidebar entry and
// the landing page can never drift apart, and the eventual flag-cleanup PR
// touches exactly one line (plus playwright.config.ts).

// Phase 0 of the Infinite-Canvas parity epic (G11): top-level canvas entry.
// Dark by default; flip VITE_FEATURE_CANVAS_NAV=true to expose it.
export const CANVAS_NAV_ENABLED = import.meta.env.VITE_FEATURE_CANVAS_NAV === 'true';
