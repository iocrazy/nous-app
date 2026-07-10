import { test, expect, type Page, type Route } from '@playwright/test';
import { setupStubbedSession, TEAM_ID } from './helpers/stubs';

/**
 * Visual regression stub spec for Projects Phase B (B1 Stage Ring cards +
 * B2 Stage Workbench + B3 data-aware Stage Suggestion) and PR-9 (G7 homepage
 * work queue + grid secondary view), checked against the approved mockups
 * (docs/superpowers/specs/2026-07-08-projects-phase-b-mockups.html for the
 * card states, 2026-07-10-projects-workspace-final.html "主页" section for
 * the queue rows).
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
 * PR-9 (G7) homepage work-queue batch fixture — deliberately ordered
 * generate-then-stalled here to prove the queue view sorts (stalled first),
 * not just passes fixtures through in fixture order. Project 3 (archived) is
 * intentionally absent — the real endpoint excludes archived projects.
 */
const PROJECT_SUGGESTIONS = [
  {
    project_id: '1',
    name: 'Spring Campaign 2026',
    stage_slug: 'storyboard',
    kind: 'storyboard_generate',
    stalled: false,
    progress: { total: 12, done: 9, empty: 3, generating: 0, failed: 0, script_count: 2, scene_count: 5 },
    action: { type: 'generate_missing_frames', label_key: 'projects.suggest.ctaGenerate', count: 3 },
    latest_activity: { kind: 'file', actor: 'HG', at: '2026-07-08T10:00:00Z', stalled: false },
  },
  {
    project_id: '2',
    name: 'Client Reel — Northwind',
    stage_slug: 'review',
    kind: 'review_nav',
    stalled: true,
    action: { type: 'navigate', tab: 'shares', label_key: 'projects.suggest.cta_review', count: null },
    latest_activity: { kind: 'stage', label: 'Review', at: '2026-07-05T00:00:00Z', stalled: true },
  },
];

/**
 * Installs the single `**\/api/v1/projects*` handler that dispatches on
 * pathname to the list / suggestions / stage-catalog / current-stage /
 * stage-suggestion fixtures. Registered after `setupStubbedSession`'s
 * catch-alls, so it wins (Playwright resolves the most-recently-registered
 * matching route first).
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
    if (pathname === '/api/v1/projects/suggestions') {
      return route.fulfill({ json: { items: PROJECT_SUGGESTIONS } });
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

  test('homepage queue: stalled row first + amber, generate CTA, toggle to grid', async ({ page }) => {
    await routeProjectsApi(page);
    await page.goto(PROJECTS_URL);

    // PR-9 (G7) — Queue is the new homepage default, not the card grid.
    await expect(page.getByTestId('projects-queue-view')).toBeVisible();
    const rows = page.getByTestId('queue-row');
    await expect(rows).toHaveCount(2);

    // Stalled project sorts first regardless of fixture order, its
    // attention dot + meta line use the stall token (amber in dark mode).
    await expect(rows.nth(0)).toContainText('Client Reel — Northwind');
    await expect(rows.nth(0).locator('[class*="var(--stall)"]').first()).toBeVisible();

    // One-click generate row sorts second, CTA text driven by action.count.
    await expect(rows.nth(1)).toContainText('Spring Campaign 2026');
    await expect(rows.nth(1).getByTestId('queue-cta')).toHaveText('Generate 3 frames');

    await page.screenshot({ path: 'e2e-artifacts/projects-list-phase-b.png', fullPage: true });

    // Toggle to Grid — the B1 stage-ring cards still render underneath.
    await page.getByTestId('home-view-grid-btn').click();
    await expect(page.getByTestId('projects-queue-view')).not.toBeVisible();
    await expect(
      page.getByRole('img', { name: 'Stage 3 of 6: Storyboarding' }),
    ).toBeVisible();
    await expect(
      page.getByRole('img', { name: 'Stage 5 of 6: Review' }),
    ).toBeVisible();
    const archivedCard = page.locator('.opacity-55', { hasText: 'Q4 Retrospective Edit' });
    await expect(archivedCard).toBeVisible();
    await expect(archivedCard.getByText('Archived')).toBeVisible();
  });

  test('homepage queue matches A mockup states — light theme', async ({ page }) => {
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

    await expect(page.getByTestId('projects-queue-view')).toBeVisible();
    await expect(page.getByText('Spring Campaign 2026')).toBeVisible();
    await expect(page.getByText('Client Reel — Northwind')).toBeVisible();

    await page.screenshot({ path: 'e2e-artifacts/projects-list-phase-b-light.png', fullPage: true });
  });

  // PR-10b (Wave 2) — VITE_FEATURE_PROJECT_WORKSPACE_V2 is now baked true in
  // playwright.config.ts's webServer build (needed for projects-workspace.spec.ts),
  // so the detail pane at `${PROJECTS_URL}/1` is the new ProjectWorkspace shell,
  // not the retired StageWorkbench surface this test originally drove
  // (`stage-history-btn` / the pills-row `StageSelector`). Updated (not
  // dropped) to assert the shell's equivalents instead: the MiniStepper is
  // the exact same component reused verbatim in WorkspaceTopBar (same
  // `stage-ministep`/`ministep-dot-*` test ids), and StageSuggestion is
  // reused as-is inside the default Overview module (same `stage-suggestion`/
  // `suggest-cta` test ids) since VITE_FEATURE_PROJECT_AI_SUGGEST is also on.
  test('workspace shell top bar + suggestion CTA render for the storyboard stage', async ({ page }) => {
    await routeProjectsApi(page, { withWorkbench: true });
    // Deep-link straight into project 1's detail view — ProjectsPage
    // auto-selects the matching row from the (stubbed) list on mount once
    // `:projectId` is present, so this skips a fragile click-through.
    await page.goto(`${PROJECTS_URL}/1`);

    // The workspace shell's top bar renders once the stage catalog +
    // current stage resolve (flags on via playwright.config.ts webServer env).
    await expect(page.getByTestId('workspace-topbar')).toBeVisible({ timeout: 10_000 });

    // D2 — the mini stepper (one dot per catalog stage) renders top-right
    // of the top bar (WorkspaceTopBar reuses MiniStepper verbatim).
    await expect(page.getByTestId('stage-ministep')).toBeVisible();
    await expect(page.getByTestId(`ministep-dot-${CURRENT_STAGE_STORYBOARD.slug}`)).toBeVisible();

    // B3 — data-aware suggestion card (reused inside the default Overview
    // module), CTA driven by `action.count`.
    await expect(page.getByTestId('stage-suggestion')).toBeVisible();
    await expect(page.getByTestId('suggest-cta')).toHaveText('Generate 3 frames');

    await page.screenshot({
      path: 'e2e-artifacts/projects-workbench-phase-b.png',
      fullPage: true,
    });
  });
});
