// e2e/canvas-nav.spec.ts
// Infinite-Canvas parity Phase 0 (G11): top-level canvas entry + landing page.
// Full-stub harness (no real backend) — see e2e/helpers/stubs.ts.
// VITE_FEATURE_CANVAS_NAV=true is baked into the playwright webServer build.

import { expect, test, type Page } from '@playwright/test';
import { setupStubbedSession, TEAM_ID } from './helpers/stubs';

const PROJECT = {
  id: 'p1',
  name: 'Demo Project',
  team_id: TEAM_ID,
  created_at: '2020-01-01T00:00:00Z',
  updated_at: '2020-01-01T00:00:00Z',
};

const CANVASES = [
  {
    id: 'c1',
    project_id: 'p1',
    name: 'Hero Canvas',
    kind: 'smart',
    viewport_json: { x: 0, y: 0, zoom: 1 },
    nodes_json: [],
    connections_json: [],
    node_ops_json: [],
    connection_ops_json: [],
    base_updated_at: '2020-01-02T00:00:00Z',
    created_at: '2020-01-01T00:00:00Z',
    updated_at: '2020-01-02T00:00:00Z',
    created_by: null,
  },
  {
    id: 'c2',
    project_id: 'p1',
    name: 'Storyline Board',
    kind: 'smart',
    viewport_json: { x: 0, y: 0, zoom: 1 },
    nodes_json: [],
    connections_json: [],
    node_ops_json: [],
    connection_ops_json: [],
    base_updated_at: '2020-01-03T00:00:00Z',
    created_at: '2020-01-01T00:00:00Z',
    updated_at: '2020-01-03T00:00:00Z',
    created_by: null,
  },
];

/** Register canvas-list stubs AFTER setupStubbedSession so they win priority. */
async function setupCanvasListStubs(page: Page): Promise<void> {
  // Single team-tree endpoint (N+1 fix): projects with embedded summaries.
  await page.route(`**/api/v1/canvases/team/${TEAM_ID}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: [
          {
            project_id: PROJECT.id,
            project_name: PROJECT.name,
            canvases: CANVASES.map((c) => ({
              id: c.id,
              name: c.name,
              kind: c.kind,
              updated_at: c.updated_at,
            })).sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1)),
          },
        ],
      }),
    }),
  );
}

test.describe('canvas nav entry + landing page (Phase 0 G11)', () => {
  test.beforeEach(async ({ page }) => {
    await page.emulateMedia({ colorScheme: 'dark' });
    // i18n defaults to 'zh' when no `language` key is stored (see i18n.ts) —
    // force English so assertions match the UI-must-be-English convention.
    await page.addInitScript(() => {
      try {
        localStorage.setItem('language', 'en');
        localStorage.setItem('mediahub.theme', 'dark');
      } catch {
        /* localStorage unavailable — nothing we can do */
      }
    });
  });

  test('sidebar Canvas item navigates to the landing page', async ({ page }) => {
    await setupStubbedSession(page);
    await setupCanvasListStubs(page);

    await page.goto(`/team/${TEAM_ID}/todolist`);
    const canvasNav = page.getByRole('button', { name: 'Canvas', exact: true });
    await expect(canvasNav).toBeVisible();
    await canvasNav.click();

    await expect(page).toHaveURL(new RegExp(`/team/${TEAM_ID}/canvas$`));
    await expect(page.getByText('Demo Project')).toBeVisible();
  });

  test('landing page lists canvases grouped by project', async ({ page }) => {
    await setupStubbedSession(page);
    await setupCanvasListStubs(page);

    await page.goto(`/team/${TEAM_ID}/canvas`);

    await expect(page.getByText('Demo Project')).toBeVisible();
    await expect(page.getByText('Storyline Board')).toBeVisible();
    await expect(page.getByText('Hero Canvas')).toBeVisible();
    await expect(page.getByText('New Canvas')).toBeVisible();

    // Newest first: Storyline Board (updated 01-03) renders before Hero
    // Canvas (01-02) — assert actual layout order, not just visibility.
    const storyline = await page.getByText('Storyline Board').boundingBox();
    const hero = await page.getByText('Hero Canvas').boundingBox();
    expect(storyline && hero).toBeTruthy();
    expect(
      storyline!.y < hero!.y || (storyline!.y === hero!.y && storyline!.x < hero!.x),
    ).toBe(true);

    await page.screenshot({ path: 'e2e-artifacts/canvas-list.png', fullPage: true });
  });

  test('canvas card opens the editor route', async ({ page }) => {
    await setupStubbedSession(page);
    await setupCanvasListStubs(page);
    // Editor load: GET /api/v1/canvases/c1 → single canvas envelope.
    await page.route('**/api/v1/canvases/c1', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ success: true, data: CANVASES[0] }),
      }),
    );

    await page.goto(`/team/${TEAM_ID}/canvas`);
    await page.getByText('Hero Canvas').click();
    await expect(page).toHaveURL(new RegExp(`/team/${TEAM_ID}/canvas/c1$`));
  });

  test('empty project list shows the empty state', async ({ page }) => {
    await setupStubbedSession(page);
    await page.route(`**/api/v1/canvases/team/${TEAM_ID}`, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ success: true, data: [] }),
      }),
    );

    await page.goto(`/team/${TEAM_ID}/canvas`);
    await expect(
      page.getByText('No projects yet — create a project to start a canvas.'),
    ).toBeVisible();
  });
});

test.describe('canvas trash (G9)', () => {
  test('delete → trash → restore round trip', async ({ page }) => {
    await page.addInitScript(() => {
      try {
        localStorage.setItem('language', 'en');
        localStorage.setItem('mediahub.theme', 'dark');
      } catch {
        /* localStorage unavailable */
      }
    });
    await setupStubbedSession(page);
    await setupCanvasListStubs(page);

    let trashed: string[] = [];
    await page.route('**/api/v1/canvases/c1', (route) => {
      if (route.request().method() === 'DELETE') {
        trashed.push('c1');
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ success: true }),
        });
      }
      return route.fallback();
    });
    await page.route(`**/api/v1/canvases/team/${TEAM_ID}/trash`, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          data: trashed.map((id) => ({
            id,
            name: 'Hero Canvas',
            kind: 'smart',
            updated_at: '2020-01-02T00:00:00Z',
            deleted_at: '2020-01-04T00:00:00Z',
            project_id: 'p1',
            project_name: 'Demo Project',
          })),
        }),
      }),
    );
    await page.route('**/api/v1/canvases/c1/restore', (route) => {
      trashed = [];
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ success: true }),
      });
    });

    await page.goto(`/team/${TEAM_ID}/canvas`);
    await expect(page.getByText('Hero Canvas')).toBeVisible();

    // Soft-delete from the card's "···" kebab menu (CanvasCardMenu) — two
    // cards on the page, scope to the one holding Hero Canvas. The direct
    // hover-revealed "Move to trash" button was consolidated into this
    // dropdown (rename/export/create-issue/delete) alongside the trash item.
    const heroCard = page
      .locator('div.group')
      .filter({ hasText: 'Hero Canvas' })
      .first();
    await heroCard.hover();
    await heroCard.getByRole('button', { name: 'Canvas actions' }).click();
    await page.getByRole('menuitem', { name: 'Move to trash' }).click();
    await expect(page.getByText('Hero Canvas')).toHaveCount(0);

    // Expand the trash — the row is there with Restore + Delete Forever.
    await page.getByRole('button', { name: /Trash/ }).click();
    await expect(page.getByText('Hero Canvas')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Delete Forever' })).toBeVisible();

    // Restore refetches the live tree (stub returns the full list again).
    await page.getByRole('button', { name: 'Restore' }).click();
    await expect(
      page.locator('section').filter({ hasText: 'Demo Project' }).getByText('Hero Canvas'),
    ).toBeVisible();
    await page.screenshot({ path: 'e2e-artifacts/canvas-trash.png', fullPage: true });
  });
});
