// e2e/canvas-feel.spec.ts
// Infinite-Canvas parity Phase 3 (G6) against the real production build:
//  - edge scissors: selecting a wire reveals a midpoint cut button; clicking
//    removes the connection
//  - Arrange: one click lays the graph out left-to-right by dependency rank
//  - bare `z`: toggles the zoom-out overview and returns to the prior viewport

import { expect, test, type Page } from '@playwright/test';
import { setupStubbedSession, TEAM_ID } from './helpers/stubs';

const CANVAS = {
  id: 'c-feel',
  project_id: 'p1',
  name: 'Feel Canvas',
  kind: 'smart',
  viewport_json: { x: 0, y: 0, zoom: 1 },
  nodes_json: [
    { id: 'shot1', type: 'shot', position: { x: 620, y: 420 }, data: { title: 'Shot', reference_resource_ids: [], notes: '' } },
    { id: 'p1', type: 'prompt', position: { x: 60, y: 60 }, data: { body: 'Prompt', provider_slug: null, agent_id: null, run_status: 'idle', resource_refs: [] } },
    { id: 'out1', type: 'output', position: { x: 320, y: 420 }, data: { kind: 'text', resource_id: null, preview_text: 'result', preview_url: null, crop_region: null } },
  ],
  connections_json: [
    { id: 'e1', source: 'shot1', target: 'p1', sourceHandle: null, targetHandle: null },
    { id: 'e2', source: 'p1', target: 'out1', sourceHandle: null, targetHandle: null },
  ],
  node_ops_json: [],
  connection_ops_json: [],
  base_updated_at: '2020-01-02T00:00:00Z',
  created_at: '2020-01-01T00:00:00Z',
  updated_at: '2020-01-02T00:00:00Z',
  created_by: null,
};

async function setupStubs(page: Page): Promise<void> {
  await page.route('**/api/v1/canvases/c-feel', (route) => {
    if (route.request().method() !== 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ success: true, data: CANVAS }),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: CANVAS }),
    });
  });
  await page.route('**/api/v1/canvases/c-feel/ops', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: { ...CANVAS } }),
    }),
  );
  await page.route('**/api/v1/resources/search*', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        results: [],
        counts: { all: 0, video: 0, image: 0, doc: 0, audio: 0, pdf: 0 },
        next_cursor: null,
      }),
    }),
  );
}

async function openCanvas(page: Page): Promise<void> {
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.addInitScript(() => {
    try {
      localStorage.setItem('language', 'en');
      localStorage.setItem('mediahub.theme', 'dark');
    } catch {
      /* localStorage unavailable */
    }
  });
  await setupStubbedSession(page);
  await setupStubs(page);
  await page.goto(`/team/${TEAM_ID}/canvas/c-feel`);
  await expect(page.locator('.react-flow__node')).toHaveCount(3);
}

test('selecting an edge shows the scissors; clicking cuts the wire', async ({ page }) => {
  await openCanvas(page);
  await expect(page.locator('.react-flow__edge')).toHaveCount(2);

  // Click a point ON the wire (the bezier's bbox centre is off the curve, so
  // a plain locator click misses the stroke's hit area).
  const mid = await page
    .locator('.react-flow__edge[data-id="e1"] .react-flow__edge-interaction')
    .evaluate((el) => {
      const path = el as SVGGeometryElement;
      const p = path.getPointAtLength(path.getTotalLength() / 2);
      const ctm = path.getScreenCTM();
      if (!ctm) throw new Error('no screen CTM for edge path');
      return { x: ctm.a * p.x + ctm.c * p.y + ctm.e, y: ctm.b * p.x + ctm.d * p.y + ctm.f };
    });
  await page.mouse.click(mid.x, mid.y);
  const scissors = page.getByRole('button', { name: 'Cut connection' });
  await expect(scissors).toBeVisible();

  await scissors.click();
  await expect(page.locator('.react-flow__edge')).toHaveCount(1);
  await expect(page.locator('.react-flow__edge[data-id="e1"]')).toHaveCount(0);
});

test('Arrange lays the graph out left-to-right by rank', async ({ page }) => {
  await openCanvas(page);

  await page.getByRole('button', { name: 'Arrange' }).click();

  const xOf = async (id: string) => {
    const transform = await page
      .locator(`.react-flow__node[data-id="${id}"]`)
      .evaluate((el) => (el as HTMLElement).style.transform);
    const m = /translate\(([-\d.]+)px/.exec(transform);
    return m ? parseFloat(m[1]) : NaN;
  };
  // shot1 → p1 → out1 must end up in ascending x order.
  const [xs, xp, xo] = await Promise.all([xOf('shot1'), xOf('p1'), xOf('out1')]);
  expect(xs).toBeLessThan(xp);
  expect(xp).toBeLessThan(xo);

  await page.screenshot({ path: 'e2e-artifacts/canvas-feel-arrange.png', fullPage: true });
});

test('mod+A then mod+D duplicates the whole graph with its wiring', async ({ page }) => {
  await openCanvas(page);
  await expect(page.locator('.react-flow__edge')).toHaveCount(2);

  await page.locator('.react-flow').click({ position: { x: 30, y: 200 } });
  await page.keyboard.press('ControlOrMeta+a');
  await page.keyboard.press('ControlOrMeta+d');

  await expect(page.locator('.react-flow__node')).toHaveCount(6);
  await expect(page.locator('.react-flow__edge')).toHaveCount(4);
});

test('bare z toggles the overview and returns', async ({ page }) => {
  await openCanvas(page);

  const viewportTransform = () =>
    page
      .locator('.react-flow__viewport')
      .evaluate((el) => (el as HTMLElement).style.transform);

  const before = await viewportTransform();
  await page.locator('.react-flow').click({ position: { x: 30, y: 200 } });
  await page.keyboard.press('z');
  await expect
    .poll(viewportTransform, { message: 'overview should change the viewport' })
    .not.toBe(before);

  await page.keyboard.press('z');
  await expect
    .poll(viewportTransform, { message: 'second z should restore the viewport' })
    .toBe(before);
});
