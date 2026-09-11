// Test-only stand-in for vite-plugin-pwa's `virtual:pwa-register`. The virtual
// module exists only when the VitePWA plugin runs (vite.config.ts); vitest uses
// vitest.config.ts, so without this alias any module importing it fails import
// analysis. Tests that care about registration vi.mock('virtual:pwa-register').
import type { RegisterSWOptions } from 'vite-plugin-pwa/types';

export function registerSW(_options?: RegisterSWOptions): (reloadPage?: boolean) => Promise<void> {
  return async () => {};
}
