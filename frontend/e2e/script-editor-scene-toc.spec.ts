// The scene navigation is a Notion-style table of contents (SceneToc): a minimal
// tick rail floating at the left edge of the paper that reveals a full scene
// panel on hover, and can be pinned open. It replaces the boxed "slim scene
// rail" from #1443 — the panel floats OVER the paper (no layout push), which was
// the whole point ("又是一个框"的解药).
//
// These gates exercise the STANDALONE fullscreen route (what the user sees in the
// screenshots). A second test drives the workspace so the EMBEDDED mount is
// covered too — both modes render the identical SceneToc.

import { expect, test, type Page, type Route } from '@playwright/test';
import { setupStubbedSession, TEAM_ID } from './helpers/stubs';
import {
  SCENE_ID_BASE,
  SCRIPT_ID,
  SCRIPT_URL,
  setupScriptStubs,
  wireScene,
  type WireElement,
} from './helpers/script-stubs';

const els = (n: number): WireElement[] => [
  { id: `el_${n}0000001`, type: 'action', text: `Action line in scene ${n}.` },
];

const SCENES = Array.from({ length: 4 }, (_, i) =>
  wireScene({ id: SCENE_ID_BASE + i, sortOrder: i, location: `LOCATION ${i + 1}`, elements: els(i) }),
);

const toc = (page: Page) => page.getByTestId('scene-toc');
const ticks = (page: Page) => page.getByTestId('scene-toc-tick');

test.describe('Scene TOC (Notion-style, standalone route)', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      try {
        localStorage.setItem('language', 'en');
      } catch {
        /* noop */
      }
    });
  });

  test('default state shows only the tick rail — the panel is not open', async ({ page }) => {
    await setupScriptStubs(page, { scenes: SCENES, format: 'hollywood' });
    await page.goto(SCRIPT_URL);

    await expect(page.locator('.mh-sheet-inner [data-scene-id]').first()).toBeVisible({
      timeout: 15_000,
    });

    // One tick per scene, and the TOC is closed (only the ticks show).
    await expect(ticks(page)).toHaveCount(SCENES.length);
    await expect(toc(page)).toHaveAttribute('data-open', 'false');
  });

  test('hover floats the panel; a row click jumps and syncs the active tick', async ({ page }) => {
    await setupScriptStubs(page, { scenes: SCENES, format: 'hollywood' });
    await page.goto(SCRIPT_URL);
    await expect(page.locator('.mh-sheet-inner [data-scene-id]').first()).toBeVisible({
      timeout: 15_000,
    });

    // Hover the tick rail → the panel opens and its SceneRail list is reachable.
    await page.locator('.mh-toc-ticks').hover();
    await expect(toc(page)).toHaveAttribute('data-open', 'true');
    const sceneRail = page.getByTestId('scene-rail');
    await expect(sceneRail.getByText('LOCATION 3')).toBeVisible();

    // Click the 3rd scene's row → the sheet jumps there and the active state
    // (both the panel row AND the matching tick) follows.
    await sceneRail.getByText('LOCATION 3').click();
    await expect(sceneRail.locator('.mh-scene-row.active')).toContainText('LOCATION 3');
    await expect(ticks(page).nth(2)).toHaveClass(/active/);
  });

  test('the panel collapses back to ticks after the pointer leaves', async ({ page }) => {
    await setupScriptStubs(page, { scenes: SCENES, format: 'hollywood' });
    await page.goto(SCRIPT_URL);
    await expect(page.locator('.mh-sheet-inner [data-scene-id]').first()).toBeVisible({
      timeout: 15_000,
    });

    await page.locator('.mh-toc-ticks').hover();
    await expect(toc(page)).toHaveAttribute('data-open', 'true');

    // Move the pointer well away from the TOC → after the grace delay it closes.
    await page.mouse.move(10, 10);
    await page.locator('.mh-sheet').hover({ position: { x: 200, y: 200 } });
    await expect(toc(page)).toHaveAttribute('data-open', 'false');
  });

  test('pin keeps the panel open and survives a reload', async ({ page }) => {
    await setupScriptStubs(page, { scenes: SCENES, format: 'hollywood' });
    await page.goto(SCRIPT_URL);
    await expect(page.locator('.mh-sheet-inner [data-scene-id]').first()).toBeVisible({
      timeout: 15_000,
    });

    await page.locator('.mh-toc-ticks').hover();
    await page.getByTestId('scene-toc-pin').click();
    await expect(toc(page)).toHaveAttribute('data-open', 'true');

    // Pinned: moving the pointer far off the TOC must NOT collapse it (a
    // far-right point clear of the 244px panel at the sheet's left edge).
    await page.mouse.move(1180, 400);
    await page.waitForTimeout(400); // past the un-pinned close delay
    await expect(toc(page)).toHaveAttribute('data-open', 'true');

    // Persisted per script: a reload comes back pinned (open).
    await page.reload();
    await expect(page.locator('.mh-sheet-inner [data-scene-id]').first()).toBeVisible({
      timeout: 15_000,
    });
    await expect(toc(page)).toHaveAttribute('data-open', 'true');
  });
});

// ── Embedded (workspace studio) mount ───────────────────────────────────────
// The SceneToc must render identically when the editor is embedded in the
// project workspace (no boxed rail there either). Reuses the workspace-entry
// stub shape (script-editor-entry.spec.ts).

const PROJECTS_URL = `/team/${TEAM_ID}/projects`;
const CATALOG = [
  { id: '1', slug: 'planning', name: 'Planning', sort_order: 1, tools_recommended: [] },
  { id: '2', slug: 'writing', name: 'Writing', sort_order: 2, tools_recommended: [] },
];
const PROJECT = {
  id: '1', name: 'Spring Campaign 2026', description: '', owner_id: 'u1', team_id: TEAM_ID,
  project_type: 'external', project_group: null, announcement: null, is_starred: false,
  color_label: null, archived_at: null, file_count: 4,
  created_at: '2026-06-01T00:00:00Z', updated_at: '2026-07-08T10:00:00Z',
};
const EPISODES = [
  { episode_id: '1', title: 'Ep 1 — Pilot', sort_order: 10, script_count: 1, scene_count: 3,
    shots_total: 0, shots_done: 0, renders_count: 0, status: 'boarding' },
];
const SCRIPTS = [
  { id: 's1', name: 'Pilot Draft', status: 'active',
    created_at: '2026-07-01T00:00:00Z', updated_at: '2026-07-08T00:00:00Z', episode_id: '1' },
];
const WS_SCENES = Array.from({ length: 3 }, (_, i) => ({
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
  await page.route(/\/api\/v1\/scripts(\/|\?|$)/, (route: Route) => {
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
    if (/^\/api\/v1\/scripts\/[^/]+\/scenes$/.test(pathname)) {
      return route.fulfill({ json: { success: true, data: WS_SCENES } });
    }
    return route.fallback();
  });
}

test('embedded (workspace) mount renders the same floating SceneToc, not a boxed rail', async ({ page }) => {
  await setupStubbedSession(page);
  await page.addInitScript(() => { try { localStorage.setItem('language', 'en'); } catch { /* noop */ } });
  await routeWorkspaceApi(page);

  await page.goto(`${PROJECTS_URL}/1`);
  await expect(page.getByTestId('workspace-topbar')).toBeVisible({ timeout: 15_000 });
  await page.getByTestId('ws-module-episodes').click();
  await expect(page.getByTestId('ws-ep-card')).toHaveText(/Ep 1 — Pilot/);
  await page.getByTestId('ws-ep-script').click();

  // Embedded shell mounts and the floating SceneToc renders (no boxed slim rail).
  await expect(page.locator('.mh-editor-shell.mh-embedded')).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId('scene-toc')).toBeVisible();
  await expect(ticks(page)).toHaveCount(WS_SCENES.length);
  await expect(page.getByTestId('scene-toc')).toHaveAttribute('data-open', 'false');
});
