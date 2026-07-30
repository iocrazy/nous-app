import { test, expect, type Page } from '@playwright/test';
import { setupStubbedSession, TEAM_ID, USER_ID } from './helpers/stubs';

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

/** i18n.ts defaults to 'zh' unless overridden (same idiom as
 * workflow-walkthrough.spec.ts) — several of the chrome probes below locate
 * elements by their English title/label text (getByTitle('Account') etc). */
async function forceEnglishLocale(page: Page): Promise<void> {
  await page.addInitScript(() => {
    try {
      localStorage.setItem('language', 'en');
    } catch {
      /* ignore */
    }
  });
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

// ============================================================================
// Chrome blast-radius probes (K1.5 review finding #1) — the module-scoped
// `--accent-text`/`--accent-soft`/`--accent-border` are ambient chrome tokens
// consumed far outside the routed page content (TopBar, MobileTabBar,
// WorkspaceSwitcher, TaskCenter all live in AppLayout's shell, not the
// Outlet subtree). This section asserts 4 named surfaces resolve to the
// CURRENT module's hue via getComputedStyle — not just "some page under
// resources renders green→steel", but that the shared shell itself repaints.
// ============================================================================

function hexToRgb(hex: string): string {
  const n = parseInt(hex.slice(1), 16);
  return `rgb(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255})`;
}

// Expected --accent-text per module × theme, mirroring the index.css
// [data-theme][data-module] blocks (K1.6: light now uses the 700 tier for
// all four hues — 600-on-100-soft-bg measured below 4.5:1 for every hue
// including the global default, task-K1-report.md §K1.6 — dark stays the
// 400 tier, verified against the actual composited --island background).
const MODULE_ACCENT_TEXT: Record<string, { light: string; dark: string }> = {
  ai: { light: hexToRgb('#69507B'), dark: hexToRgb('#AF9BBF') },
  inspiration: { light: hexToRgb('#805C1B'), dark: hexToRgb('#C6A675') },
  resources: { light: hexToRgb('#3A607A'), dark: hexToRgb('#8DA9BE') },
};

for (const theme of ['dark', 'light'] as const) {
  for (const route of ROUTES) {
    test(`${theme}: ${route.name} TopBar avatar bubble follows module accent`, async ({ page }) => {
      await setupStubbedSession(page);
      await forceTheme(page, theme);
      await forceEnglishLocale(page);
      await page.goto(route.path);
      await expect(page.locator('.island-frame').first()).toBeVisible({ timeout: 15_000 });

      const avatarButton = page.getByTitle('Account');
      await expect(avatarButton).toBeVisible();
      const color = await avatarButton.locator('div.rounded-full').evaluate(
        (el) => getComputedStyle(el).color,
      );
      expect(color).toBe(MODULE_ACCENT_TEXT[route.module][theme]);
    });
  }
}

// MobileTabBar has no "AI Library" destination at all (AppLayout's
// `mobileTabs` config is a fixed {Parser, Downloads, Resources, Tasks} set —
// see components/AppLayout.tsx) — so this surface only applies to
// inspiration (the "Parser" tab IS Topic Inspiration) and resources.
// ai-library is intentionally absent, not a bug to force a probe against.
//
// inspiration used to be excluded here too: at a mobile (390px) viewport,
// navigating to /parser reliably crashed into React Router's default
// ErrorBoundary (`TypeError: u is not iterable` inside TopicInspirationPage).
// Root-caused and fixed in task-M1. The fix that actually matters on THIS
// build is `services/inspirationService.ts`'s `listNotes`/`getActivity`/
// `getTagCounts` — `.env.production` sets `VITE_FEATURE_INSPIRATION_NOTES=
// true`, so on the production build this Playwright suite exercises,
// `TopicInspirationPage` early-returns `<InspirationPage/>` before any
// `topicService` call is even made; the crash is `InspirationPage`'s
// `listNotes()` returning the e2e stub harness's generic catch-all body
// (`{ success: true, data: [] }`, a non-array object) with no runtime shape
// check, which then broke `NoteTimeline`'s `.filter`-based grouping.
// `services/topicService.ts` got the same defensive fix in the same task
// (it had the identical blind-cast anti-pattern), but that path is dead code
// on this specific build/route combination. Both are now routed through a
// `toArray` helper that degrades to `[]` instead — see task-M1-report.md and
// services/inspirationService.test.ts / pages/TopicInspirationPage.test.tsx
// for the regression coverage. Re-included now that the crash no longer
// reproduces.
const MOBILE_TAB_ROUTES = ROUTES.filter((r) => r.module === 'resources' || r.module === 'inspiration');

for (const theme of ['dark', 'light'] as const) {
  for (const route of MOBILE_TAB_ROUTES) {
    test(`${theme}: ${route.name} MobileTabBar active tab follows module accent`, async ({ page }) => {
      await page.setViewportSize({ width: 390, height: 844 });
      await setupStubbedSession(page);
      await forceTheme(page, theme);
      await forceEnglishLocale(page);
      await page.goto(route.path);
      // .island-frame is desktop-only (`hidden sm:flex` — IslandShell.tsx) and
      // never renders at this mobile viewport. The tab bar's OWN outer
      // wrapper (`.fixed.inset-x-0.bottom-0`) has zero intrinsic height (its
      // children are absolutely positioned), which trips Playwright's
      // bounding-box visibility check even though it's `display:block` —
      // so scope straight to the button (which has real dimensions), still
      // narrowed to that wrapper to avoid matching an unrelated same-labelled
      // element elsewhere on the page.
      const tabLabel = route.module === 'inspiration' ? 'Parser' : 'Resources';
      const activeTab = page.locator('div.fixed.inset-x-0.bottom-0 button', { hasText: tabLabel });
      await expect(activeTab).toBeVisible({ timeout: 15_000 });
      // transition-colors is a CSS transition — settle before sampling so the
      // read isn't a mid-transition blend.
      await page.waitForTimeout(500);
      const color = await activeTab.evaluate((el) => getComputedStyle(el).color);
      expect(color).toBe(MODULE_ACCENT_TEXT[route.module][theme]);
    });
  }
}

for (const theme of ['dark', 'light'] as const) {
  for (const route of ROUTES) {
    test(`${theme}: ${route.name} WorkspaceSwitcher checkmark follows module accent`, async ({ page }) => {
      await setupStubbedSession(page);
      await forceTheme(page, theme);
      // displayName copy ("…'s Workspace") is otherwise zh by default.
      await forceEnglishLocale(page);
      await page.goto(route.path);
      await expect(page.locator('.island-frame').first()).toBeVisible({ timeout: 15_000 });

      const trigger = page.getByRole('button', { name: /Workspace/ }).first();
      await expect(trigger).toBeVisible();
      await trigger.click();

      // Personal workspace row (the only row — no teams in this fixture): its
      // Check icon is `text-[var(--accent-text)]` (WorkspaceSwitcher.tsx). The
      // dropdown row is a SECOND "…Workspace" button (the first is the
      // trigger itself), so `.last()` reaches into the open dropdown.
      const activeRow = page.getByRole('button', { name: /Workspace/ }).last();
      const svgColor = await activeRow.locator('svg').last().evaluate((el) => getComputedStyle(el).color);
      expect(svgColor).toBe(MODULE_ACCENT_TEXT[route.module][theme]);
    });
  }
}

// TaskCenter row (hover-revealed action icon) — needs a completed task with
// a resource_id so taskRowActions() exposes `open`/`download` (see
// components/TaskCenter/taskRowPresentation.ts), which is what carries
// `hover:text-[var(--accent-text)]`.
async function stubOneCompletedTask(page: Page): Promise<void> {
  await page.route('**/api/v1/task-manager/tasks*', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data: [
          {
            dbos_workflow_id: 'task-e2e-1',
            user_id: USER_ID,
            task_type: 'download',
            status: 'completed',
            title: 'E2E Completed Task',
            progress: 100,
            resource_id: 'res-e2e-1',
            metadata: {},
            created_at: '2020-01-01T00:00:00Z',
          },
        ],
        total: 1,
        page: 1,
        page_size: 20,
      }),
    }),
  );
}

// inspiration (/parser) used to be excluded here too — the SAME
// pre-existing `TypeError: u is not iterable` TopicInspirationPage crash
// (React Router's default ErrorBoundary replacing the page), reproduced on a
// DEFAULT (desktop) viewport under this exact probe, i.e. not only the
// mobile-viewport-gated case the MobileTabBar probe above hit. Root-caused
// and fixed in task-M1 — see the MOBILE_TAB_ROUTES comment above for why the
// fix that matters on this build is `services/inspirationService.ts`, not
// `topicService.ts` — re-included now that the crash no longer reproduces.
const TASK_CENTER_ROUTES = ROUTES;

for (const theme of ['dark', 'light'] as const) {
  for (const route of TASK_CENTER_ROUTES) {
    test(`${theme}: ${route.name} TaskCenter row hover-action follows module accent`, async ({ page }) => {
      await setupStubbedSession(page);
      await stubOneCompletedTask(page);
      await forceTheme(page, theme);
      await forceEnglishLocale(page);
      await page.goto(route.path);
      await expect(page.locator('.island-frame').first()).toBeVisible({ timeout: 15_000 });

      await page.getByTitle('Task Center').click();
      // Panel defaults to the "Active" tab (TaskCenterPanel.tsx); a completed
      // task lands in "History" (isActiveItem() only counts pending/
      // processing) — switch tabs before looking for the row.
      await page.getByRole('button', { name: 'History' }).click();
      const row = page.getByText('E2E Completed Task');
      await expect(row).toBeVisible({ timeout: 15_000 });
      await row.hover();
      const openBtn = page.getByTitle('Open');
      // opacity-0 group-hover:opacity-100 — Playwright's visibility check
      // ignores opacity (only display/visibility/size), so this can resolve
      // before the hover-reveal CSS transition is done; wait for the actual
      // opacity to settle before hovering + sampling the color transition.
      await expect(openBtn).toBeVisible();
      await expect(openBtn).toHaveCSS('opacity', '1');
      await openBtn.hover();
      await expect(openBtn).toHaveCSS('color', MODULE_ACCENT_TEXT[route.module][theme]);
      const color = await openBtn.evaluate((el) => getComputedStyle(el).color);
      expect(color).toBe(MODULE_ACCENT_TEXT[route.module][theme]);
    });
  }
}
