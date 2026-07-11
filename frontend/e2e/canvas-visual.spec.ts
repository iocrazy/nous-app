// e2e/canvas-visual.spec.ts
// Infinite-Canvas parity Phase 0 (G10): canvas token base. Verifies the
// --canvas-* tokens actually drive the engine surface in BOTH themes (the
// legacy canvas vars were dark-only) and leaves screenshots for the visual
// pass. Full-stub harness — see e2e/helpers/stubs.ts.

import { expect, test, type Page } from '@playwright/test';
import { setupStubbedSession, TEAM_ID } from './helpers/stubs';

const CANVAS = {
  id: 'c-visual',
  project_id: 'p1',
  name: 'Visual Canvas',
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
};

async function setupCanvasStubs(page: Page): Promise<void> {
  await page.route('**/api/v1/canvases/c-visual', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: CANVAS }),
    }),
  );
}

async function seedTheme(page: Page, theme: 'dark' | 'light'): Promise<void> {
  await page.emulateMedia({ colorScheme: theme });
  await page.addInitScript(
    ([t]) => {
      try {
        localStorage.setItem('language', 'en');
        localStorage.setItem('mediahub.theme', t as string);
      } catch {
        /* localStorage unavailable — nothing we can do */
      }
    },
    [theme] as const,
  );
}

test.describe('canvas token base (Phase 0 G10)', () => {
  test('dark theme: engine surface uses --canvas-page', async ({ page }) => {
    await seedTheme(page, 'dark');
    await setupStubbedSession(page);
    await setupCanvasStubs(page);

    await page.goto(`/team/${TEAM_ID}/canvas/c-visual`);
    // Guard against the always-true trap: dark values also live on :root, so
    // first prove the theme attribute itself landed.
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    const engine = page.locator('.mh-canvas').first();
    await expect(engine).toBeVisible();

    // --canvas-page dark = #0f141d
    await expect(engine).toHaveCSS('background-color', 'rgb(15, 20, 29)');
    await page.screenshot({ path: 'e2e-artifacts/canvas-visual-dark.png', fullPage: true });
  });

  test('light theme: engine surface flips with [data-theme]', async ({ page }) => {
    await seedTheme(page, 'light');
    await setupStubbedSession(page);
    await setupCanvasStubs(page);

    await page.goto(`/team/${TEAM_ID}/canvas/c-visual`);
    const engine = page.locator('.mh-canvas').first();
    await expect(engine).toBeVisible();

    // --canvas-page light = #f8fafc — the legacy dark-only vars could never do this.
    await expect(engine).toHaveCSS('background-color', 'rgb(248, 250, 252)');
    await page.screenshot({ path: 'e2e-artifacts/canvas-visual-light.png', fullPage: true });
  });
});

test('ports fade in on node hover (P1-9)', async ({ page }) => {
  await seedTheme(page, 'dark');
  await setupStubbedSession(page);
  await page.route('**/api/v1/canvases/c-ports', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: {
          ...CANVAS,
          id: 'c-ports',
          nodes_json: [
            {
              id: 'p1',
              type: 'prompt',
              position: { x: 120, y: 120 },
              data: { body: 'x', provider_slug: '', agent_id: null, run_status: 'idle', resource_refs: [] },
            },
          ],
        },
      }),
    }),
  );
  await page.route('**/api/v1/resources/search*', (r) =>
    r.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ results: [], counts: { all: 0, video: 0, image: 0, doc: 0, audio: 0, pdf: 0 }, next_cursor: null }),
    }),
  );
  await page.goto(`/team/${TEAM_ID}/canvas/c-ports`);
  const node = page.locator('.react-flow__node').first();
  await expect(node).toBeVisible();
  const handle = node.locator('.react-flow__handle').first();
  const opacityOf = () =>
    handle.evaluate((el) => getComputedStyle(el as HTMLElement).opacity);
  // Idle: invisible (hit area stays live — visual only).
  await expect.poll(opacityOf).toBe('0');
  await node.hover();
  await expect.poll(opacityOf).toBe('1');
});
