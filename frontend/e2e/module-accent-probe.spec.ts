import { test, expect, type Page } from '@playwright/test';
import { setupStubbedSession, TEAM_ID } from './helpers/stubs';

/**
 * K1.5 (2026-07-29) — per-module accent scoping probe (docs/superpowers/specs/
 * 2026-07-29-warm-paper-palette-design.md §5).
 *
 * AppLayout stamps `<html data-module="…">` from `viewToModule(pathnameToView(
 * location.pathname))` (utils/routeConfig.ts). This is a minimal visual probe
 * — not a full walkthrough — for the 3 modules that get a non-default accent:
 * AI Library (plum), Topic Inspiration (ochre), Resources (steel). Projects
 * is already covered by e2e/workflow-walkthrough.spec.ts (green = the global
 * default, no override to verify).
 *
 * Full-stub harness (same contract as workflow-walkthrough): the generic
 * catch-alls in setupStubbedSession return empty lists/objects for any
 * unmatched `/api/v1/**` or `/rest/v1/**` call, so these pages render their
 * empty state without a real backend.
 *
 * Screenshots land in test-results/module-accent/ for human review.
 */

const SHOTS = 'test-results/module-accent';

async function forceTheme(page: Page, theme: 'dark' | 'light'): Promise<void> {
  await page.addInitScript((t) => {
    try {
      localStorage.setItem('mediahub.theme', t as string);
    } catch {
      /* ignore */
    }
  }, theme);
}

const ROUTES: { name: string; path: string; module: string }[] = [
  { name: 'ai-library', path: `/team/${TEAM_ID}/ai-library/agents`, module: 'ai' },
  { name: 'inspiration', path: `/team/${TEAM_ID}/parser`, module: 'inspiration' },
  { name: 'resources', path: `/team/${TEAM_ID}/resources`, module: 'resources' },
];

for (const theme of ['dark', 'light'] as const) {
  for (const route of ROUTES) {
    test(`${theme}: ${route.name} stamps data-module="${route.module}" and renders`, async ({ page }) => {
      await setupStubbedSession(page);
      await forceTheme(page, theme);
      await page.goto(route.path);
      // Generic "the shell rendered" signal shared by every authenticated
      // route (island-frame is the shell wrapper — see index.css); avoids
      // coupling this probe to any one page's internal testids.
      await expect(page.locator('.island-frame').first()).toBeVisible({ timeout: 15_000 });

      const dataModule = await page.evaluate(() => document.documentElement.dataset.module);
      expect(dataModule).toBe(route.module);

      await page.screenshot({ path: `${SHOTS}/${route.name}-${theme}.png`, fullPage: true });
    });
  }
}
