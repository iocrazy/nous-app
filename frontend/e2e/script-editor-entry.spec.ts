import { test, expect, type Page, type Route } from '@playwright/test';
import { setupStubbedSession, TEAM_ID } from './helpers/stubs';

/**
 * Regression for the "script entry flash" bug: clicking the Script child in the
 * project workspace briefly showed the EMPTY-SCRIPT keyboard-hint page (Tab /
 * Enter / @) before a non-empty script's scenes finished loading — because
 * `scriptUntouched` (`[].every(...)`) is true for the still-loading empty scene
 * array, and the hint was not gated on `loadState === 'ready'`. The intended
 * `.mh-shell-state` "Loading…" overlay also painted BEHIND the paper, so the
 * empty state was fully visible.
 *
 * The scenes response is held behind an explicit gate so the assertion window
 * (shell mounted, scenes not yet arrived) is deterministic rather than timing
 * dependent. Same network-stub approach as projects-workspace.spec.ts.
 */

const PROJECTS_URL = `/team/${TEAM_ID}/projects`;

const CATALOG = [
  { id: '1', slug: 'planning', name: 'Planning', sort_order: 1, tools_recommended: [] },
  { id: '2', slug: 'writing', name: 'Writing', sort_order: 2, tools_recommended: [] },
  { id: '3', slug: 'storyboard', name: 'Storyboard', sort_order: 3, tools_recommended: [] },
];

const PROJECT = {
  id: '1', name: 'Spring Campaign 2026', description: '', owner_id: 'u1', team_id: TEAM_ID,
  project_type: 'external', project_group: null, announcement: null, is_starred: false,
  color_label: null, archived_at: null, file_count: 12,
  created_at: '2026-06-01T00:00:00Z', updated_at: '2026-07-08T10:00:00Z',
};

const EPISODES = [
  { episode_id: '1', title: 'Ep 1 — Pilot', sort_order: 10, script_count: 1, scene_count: 18,
    shots_total: 12, shots_done: 9, renders_count: 1, status: 'boarding' },
];

const SCRIPTS = [
  { id: 's1', name: 'Pilot Draft', status: 'active',
    created_at: '2026-07-01T00:00:00Z', updated_at: '2026-07-08T00:00:00Z', episode_id: '1' },
];

// Scenes carry real content, so once loaded the script is NOT untouched — the
// keyboard hint must never appear for this script at any point.
const SCENES = Array.from({ length: 6 }, (_, i) => ({
  id: 900000 + i,
  script_id: 100,
  chapter_id: null,
  heading_int_ext: 'INT',
  location_text: `Location ${i + 1}`,
  time_of_day: 'DAY',
  content_version: 1,
  content_json: [{ id: `el-${i}`, type: 'action', text: `Scene ${i + 1} body text.` }],
  sort_order: i,
}));

async function routeWorkspaceApi(page: Page): Promise<void> {
  await page.route(/\/api\/v1\/projects(\/|\?|$)/, (route: Route) => {
    const { pathname } = new URL(route.request().url());
    if (pathname === '/api/v1/projects') return route.fulfill({ json: { data: [PROJECT] } });
    if (pathname === '/api/v1/projects/stages/catalog') return route.fulfill({ json: { data: CATALOG } });
    if (pathname.endsWith('/episodes/progress')) return route.fulfill({ json: { success: true, data: EPISODES } });
    if (pathname.endsWith('/entities')) return route.fulfill({ json: { success: true, data: { characters: [], locations: [] } } });
    if (pathname.endsWith('/episodes')) return route.fulfill({ json: { success: true, data: EPISODES } });
    return route.fallback();
  });
}

/**
 * @param gate a promise the scenes response awaits before resolving. The test
 *   holds it open to keep the editor in its loading window, then releases it.
 */
async function routeScriptsApi(page: Page, gate: Promise<void>): Promise<void> {
  await page.route(/\/api\/v1\/scripts(\/|\?|$)/, async (route: Route) => {
    const { pathname } = new URL(route.request().url());
    if (pathname === '/api/v1/scripts/projects') {
      return route.fulfill({ json: { success: true, data: { items: SCRIPTS, total: SCRIPTS.length } } });
    }
    const detail = pathname.match(/^\/api\/v1\/scripts\/projects\/([^/]+)$/);
    if (detail) {
      return route.fulfill({ json: { success: true, data: {
        project: { id: detail[1], project_id: PROJECT.id, episode_id: '1', name: 'Pilot Draft' },
        chapters: [],
      } } });
    }
    const scenes = pathname.match(/^\/api\/v1\/scripts\/([^/]+)\/scenes$/);
    if (scenes) {
      await gate; // hold the editor in its loading state until the test releases it
      return route.fulfill({ json: { success: true, data: SCENES } });
    }
    return route.fallback();
  });
}

test.describe('Script editor entry (workspace)', () => {
  test.beforeEach(async ({ page }) => {
    await setupStubbedSession(page);
    await page.addInitScript(() => { try { localStorage.setItem('language', 'en'); } catch { /* noop */ } });
  });

  test('opening a non-empty script never flashes the empty-script hint before scenes load', async ({ page }) => {
    let releaseScenes = () => {};
    const gate = new Promise<void>((resolve) => { releaseScenes = resolve; });

    await routeWorkspaceApi(page);
    await routeScriptsApi(page, gate);

    await page.goto(`${PROJECTS_URL}/1`);
    await expect(page.getByTestId('workspace-topbar')).toBeVisible({ timeout: 15_000 });

    // Expand the Episodes tree and open the Script work view.
    await page.getByTestId('ws-module-episodes').click();
    await expect(page.getByTestId('ws-ep-card')).toHaveText(/Ep 1 — Pilot/);
    await page.getByTestId('ws-ep-script').click();

    // The embedded editor shell mounts and enters its loading state while the
    // scenes response is held by the gate.
    await expect(page.locator('[data-editor-shell]')).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('.mh-shell-state')).toBeVisible();

    // BUG GUARD: while scenes are still loading, the empty-script keyboard hint
    // must NOT render (this is what flashed before the fix). Give it a beat to
    // ensure any errant render would have committed.
    await page.waitForTimeout(250);
    await expect(page.locator('.mh-keyboard-hint')).toHaveCount(0);

    // Release the scenes → the real editor renders the loaded content, still no
    // keyboard hint (the script is non-empty), and the loading overlay is gone.
    releaseScenes();
    await expect(page.locator('.mh-sheet-inner [data-scene-id]').first()).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('.mh-sheet-inner [data-scene-id]')).toHaveCount(SCENES.length);
    await expect(page.locator('.mh-keyboard-hint')).toHaveCount(0);
    await expect(page.locator('.mh-shell-state')).toHaveCount(0);
  });
});
