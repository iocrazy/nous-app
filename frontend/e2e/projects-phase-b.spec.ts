import { test, expect, type Page, type Route } from '@playwright/test';
import { setupStubbedSession, TEAM_ID } from './helpers/stubs';

/**
 * Visual regression stub spec for Projects Phase B (B1 Stage Ring cards +
 * B2 Stage Workbench + B3 data-aware Stage Suggestion), checked against the
 * approved "A · Stage Ring" mockup (docs/superpowers/specs/2026-07-08...).
 *
 * MediaHub has no runnable dev backend for e2e (see helpers/stubs.ts), so the
 * whole `/api/v1/projects*` surface is intercepted at the network layer with
 * deterministic fixtures. `setupStubbedSession` seeds the Supabase auth
 * session + team resolution the same way `storyboard.spec.ts` does; this spec
 * only adds the projects-specific routes on top.
 */

const PROJECTS_URL = `/team/${TEAM_ID}/projects`;

const CATALOG = [
  { id: '1', slug: 'planning', name: 'Planning', sort_order: 1, tools_recommended: [] },
  { id: '2', slug: 'script', name: 'Script', sort_order: 2, tools_recommended: [] },
  { id: '3', slug: 'storyboard', name: 'Storyboard', sort_order: 3, tools_recommended: [] },
  { id: '4', slug: 'generation', name: 'Generation', sort_order: 4, tools_recommended: [] },
  { id: '5', slug: 'review', name: 'Review', sort_order: 5, tools_recommended: [] },
  { id: '6', slug: 'delivery', name: 'Delivery', sort_order: 6, tools_recommended: [] },
];

const CURRENT_STAGE_STORYBOARD = {
  id: '3',
  slug: 'storyboard',
  name: 'Storyboard',
  sort_order: 3,
  tools_recommended: [],
};

/** The three A-mockup card states: active w/ file activity, stalled-in-stage, archived. */
const PROJECTS = [
  {
    id: '1',
    name: 'Spring Campaign 2026',
    description: 'Short-form ad series',
    owner_id: 'u1',
    team_id: TEAM_ID,
    project_type: 'external',
    project_group: null,
    announcement: null,
    is_starred: false,
    color_label: null,
    archived_at: null,
    file_count: 128,
    created_at: '2026-06-01T00:00:00Z',
    updated_at: '2026-07-08T10:00:00Z',
    current_stage: { slug: 'storyboard', name: 'Storyboarding', index: 3, total: 6 },
    latest_activity: { kind: 'file', actor: 'HG', at: '2026-07-08T10:00:00Z', stalled: false },
    members_preview: { count: 4, members: [{ user_id: 'u1', username: 'HG' }] },
  },
  {
    id: '2',
    name: 'Client Reel — Northwind',
    description: null,
    owner_id: 'u1',
    team_id: TEAM_ID,
    project_type: 'external',
    project_group: null,
    announcement: null,
    is_starred: false,
    color_label: null,
    archived_at: null,
    file_count: 64,
    created_at: '2026-05-01T00:00:00Z',
    updated_at: '2026-07-05T00:00:00Z',
    current_stage: { slug: 'review', name: 'Review', index: 5, total: 6 },
    latest_activity: { kind: 'stage', label: 'Review', at: '2026-07-05T00:00:00Z', stalled: true },
  },
  {
    id: '3',
    name: 'Q4 Retrospective Edit',
    description: null,
    owner_id: 'u1',
    team_id: TEAM_ID,
    project_type: 'internal',
    project_group: null,
    announcement: null,
    is_starred: false,
    color_label: null,
    archived_at: '2026-01-01T00:00:00Z',
    file_count: 211,
    created_at: '2025-11-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
];

const STORYBOARD_SUGGESTION = {
  stage_slug: 'storyboard',
  kind: 'storyboard_generate',
  progress: { total: 12, done: 9, empty: 3, generating: 0, failed: 0, script_count: 2, scene_count: 5 },
  action: { type: 'generate_missing_frames', label_key: 'projects.suggest.ctaGenerate', count: 3 },
};

/**
 * Installs the single `**\/api/v1/projects*` handler that dispatches on
 * pathname to the list / stage-catalog / current-stage / stage-suggestion
 * fixtures. Registered after `setupStubbedSession`'s catch-alls, so it wins
 * (Playwright resolves the most-recently-registered matching route first).
 */
async function routeProjectsApi(
  page: Page,
  opts: { withWorkbench?: boolean } = {},
): Promise<void> {
  // A glob's trailing `*` does not cross `/` boundaries, so `**/api/v1/projects*`
  // matches the bare list endpoint but silently misses nested paths like
  // `/1/current_stage` or `/stages/catalog` (they'd fall through to the real
  // network / SPA server instead of this stub). A RegExp on the pathname
  // substring matches every depth uniformly.
  await page.route(/\/api\/v1\/projects(\/|\?|$)/, (route: Route) => {
    const { pathname } = new URL(route.request().url());

    if (pathname === '/api/v1/projects') {
      return route.fulfill({ json: { data: PROJECTS } });
    }
    if (opts.withWorkbench && pathname === '/api/v1/projects/stages/catalog') {
      return route.fulfill({ json: { data: CATALOG } });
    }
    if (opts.withWorkbench && pathname.endsWith('/current_stage')) {
      return route.fulfill({ json: { data: CURRENT_STAGE_STORYBOARD } });
    }
    if (opts.withWorkbench && pathname.endsWith('/stage-suggestion')) {
      return route.fulfill({ json: STORYBOARD_SUGGESTION });
    }
    return route.fallback();
  });
}

test.describe('Projects Phase B — Stage Ring alignment', () => {
  test.beforeEach(async ({ page }) => {
    // Force dark colorScheme so the CI screenshots match the dark "A · Stage
    // Ring" mockup (docs/superpowers/specs/2026-07-08...) instead of
    // whatever OS-level scheme the headless browser defaults to.
    await page.emulateMedia({ colorScheme: 'dark' });
    await setupStubbedSession(page);
    // i18n defaults to 'zh' when no `language` key is stored (see i18n.ts).
    // Force English so text assertions and the screenshot match the UI-must-
    // be-English convention (CLAUDE.md) and the A mockup, which is English.
    // Also pin the theme preference explicitly (mirrors the language seed
    // above) — ThemeContext defaults to 'system' which would otherwise
    // resolve off the emulated media query alone.
    await page.addInitScript(() => {
      try {
        localStorage.setItem('language', 'en');
        localStorage.setItem('mediahub.theme', 'dark');
      } catch {
        /* localStorage unavailable — nothing we can do */
      }
    });
  });

  test('list cards match A mockup states', async ({ page }) => {
    await routeProjectsApi(page);
    await page.goto(PROJECTS_URL);

    await expect(page.getByText('Spring Campaign 2026')).toBeVisible();
    await expect(page.getByText('Client Reel — Northwind')).toBeVisible();
    await expect(page.getByText('Q4 Retrospective Edit')).toBeVisible();

    // B1 — segmented stage ring renders for cards with a current_stage.
    await expect(
      page.getByRole('img', { name: 'Stage 3 of 6: Storyboarding' }),
    ).toBeVisible();
    await expect(
      page.getByRole('img', { name: 'Stage 5 of 6: Review' }),
    ).toBeVisible();

    // B1 — stalled stage activity renders the stall-token dot (card 2); the
    // healthy card's dot is emerald, so this selector uniquely identifies it.
    await expect(page.locator('[class*="var(--stall)"]').first()).toBeVisible();

    // Archived card (card 3) renders at reduced opacity, no stage ring.
    const archivedCard = page.locator('.opacity-55', { hasText: 'Q4 Retrospective Edit' });
    await expect(archivedCard).toBeVisible();
    await expect(archivedCard.getByText('Archived')).toBeVisible();

    await page.screenshot({ path: 'e2e-artifacts/projects-list-phase-b.png', fullPage: true });
  });

  test('list cards match A mockup states — light theme', async ({ page }) => {
    // Same fixtures/assertions as the dark-theme run above, but forces the
    // light color scheme + `mediahub.theme=light` (D5 stall-token needs
    // coverage in both grounds — see the light [data-theme="light"] override
    // block in index.css). Registered after beforeEach's dark-forcing
    // addInitScript, so this one wins (init scripts run in registration
    // order on every navigation).
    await page.emulateMedia({ colorScheme: 'light' });
    await page.addInitScript(() => {
      try {
        localStorage.setItem('mediahub.theme', 'light');
      } catch {
        /* localStorage unavailable — nothing we can do */
      }
    });

    await routeProjectsApi(page);
    await page.goto(PROJECTS_URL);

    await expect(page.getByText('Spring Campaign 2026')).toBeVisible();
    await expect(page.getByText('Client Reel — Northwind')).toBeVisible();
    await expect(page.getByText('Q4 Retrospective Edit')).toBeVisible();

    await page.screenshot({ path: 'e2e-artifacts/projects-list-phase-b-light.png', fullPage: true });
  });

  test('storyboard suggestion card shows generate CTA', async ({ page }) => {
    await routeProjectsApi(page, { withWorkbench: true });
    // Deep-link straight into project 1's detail view — ProjectsPage
    // auto-selects the matching row from the (stubbed) list on mount once
    // `:projectId` is present, so this skips a fragile click-through.
    await page.goto(`${PROJECTS_URL}/1`);

    // B2 — workbench header renders once the stage catalog + current stage
    // resolve (flag on via playwright.config.ts webServer env).
    await expect(page.getByTestId('stage-history-btn')).toBeVisible({ timeout: 10_000 });

    // D2 — pills row is gone; the mini stepper (one dot per catalog stage)
    // renders in its place, top-right of the stage card.
    await expect(page.getByTestId('stage-ministep')).toBeVisible();
    await expect(page.getByTestId(`ministep-dot-${CURRENT_STAGE_STORYBOARD.slug}`)).toBeVisible();

    // B3 — data-aware suggestion card, CTA driven by `action.count`.
    await expect(page.getByTestId('stage-suggestion')).toBeVisible();
    await expect(page.getByTestId('suggest-cta')).toHaveText('Generate 3 frames');

    await page.screenshot({
      path: 'e2e-artifacts/projects-workbench-phase-b.png',
      fullPage: true,
    });
  });
});
