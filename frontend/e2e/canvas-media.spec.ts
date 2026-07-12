// e2e/canvas-media.spec.ts
// Infinite-Canvas parity Phase 3 (G7) against the real production build:
// clicking an output image opens the fullscreen lightbox; arrows navigate;
// a slot with history offers the Compare slider; gen slots show Rerun.

import { expect, test, type Page } from '@playwright/test';
import { setupStubbedSession, TEAM_ID } from './helpers/stubs';

// Visible colored SVG data URLs (512×512) — instant, zero network, and big
// enough to eyeball the lightbox layout in the artifact screenshots.
const svg = (color: string) =>
  `data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='512' height='512'><rect width='512' height='512' fill='%23${color}'/></svg>`;
const PNG = svg('e11d48'); // rose — current versions
const PNG2 = svg('10b981'); // emerald — second image
const PNG_OLD = svg('6366f1'); // indigo — archived history version

const CANVAS = {
  id: 'c-media',
  project_id: 'p1',
  name: 'Media Canvas',
  kind: 'smart',
  viewport_json: { x: 0, y: 0, zoom: 1 },
  nodes_json: [
    {
      id: 'p1',
      type: 'prompt',
      position: { x: 40, y: 60 },
      data: { body: 'a cat', provider_slug: 'jimeng', agent_id: null, run_status: 'succeeded', resource_refs: [], gen: { kind: 'image', model: 'm', aspect: '1:1', count: 2 } },
    },
    {
      id: 'out1',
      type: 'output',
      position: { x: 420, y: 60 },
      data: {
        kind: 'image',
        resource_id: null,
        preview_text: '',
        preview_url: PNG,
        crop_region: null,
        images: [
          { url: PNG, kind: 'image', name: 'one.png' },
          { url: PNG2, kind: 'image', name: 'two.png' },
        ],
        gen_slot: { node_id: 'p1', index: 0 },
      },
    },
    {
      id: 'hist1',
      type: 'output',
      position: { x: 420, y: 420 },
      data: {
        kind: 'image',
        resource_id: null,
        preview_text: 'History',
        preview_url: null,
        crop_region: null,
        images: [{ url: PNG_OLD, kind: 'image', name: 'old.png' }],
        history_for: 'out1',
      },
    },
  ],
  connections_json: [
    { id: 'e1', source: 'p1', target: 'out1', sourceHandle: null, targetHandle: null },
  ],
  node_ops_json: [],
  connection_ops_json: [],
  base_updated_at: '2020-01-02T00:00:00Z',
  created_at: '2020-01-01T00:00:00Z',
  updated_at: '2020-01-02T00:00:00Z',
  created_by: null,
};

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
  await page.route('**/api/v1/canvases/c-media', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: CANVAS }),
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
  await page.goto(`/team/${TEAM_ID}/canvas/c-media`);
  await expect(page.locator('.react-flow__node')).toHaveCount(3);
}

test('image click opens the lightbox; arrows navigate; Escape closes', async ({ page }) => {
  await openCanvas(page);

  await page
    .locator('[data-testid="output-images-grid"] img')
    .first()
    .click();
  const lightbox = page.getByTestId('output-lightbox');
  await expect(lightbox).toBeVisible();
  // MUST truly cover the viewport — without a body portal, RF's transformed
  // ancestors collapse position:fixed into a small box inside the node.
  const viewport = page.viewportSize();
  const box = await lightbox.boundingBox();
  expect(box?.width).toBe(viewport?.width);
  expect(box?.height).toBe(viewport?.height);
  expect(box?.x).toBe(0);
  expect(box?.y).toBe(0);
  await expect(page.getByTestId('lightbox-counter')).toHaveText('1 / 2');

  await page.getByRole('button', { name: 'Next' }).click();
  await expect(page.getByTestId('lightbox-counter')).toHaveText('2 / 2');

  await page.screenshot({ path: 'e2e-artifacts/canvas-media-lightbox.png', fullPage: true });
  await page.keyboard.press('Escape');
  await expect(lightbox).toHaveCount(0);
});

test('history-backed slot offers Compare with a draggable divider', async ({ page }) => {
  await openCanvas(page);

  await page.locator('[data-testid="output-images-grid"] img').first().click();
  await page.getByRole('button', { name: 'Compare' }).click();

  const result = page.getByTestId('compare-result');
  await expect(result).toBeVisible();

  // P1-4: the divider itself is the drag target — grab its grip and pull
  // it to 20% of the compare stage; the result layer clips to match.
  const stage = await page.getByTestId('compare-stage').boundingBox();
  const divider = await page.getByTestId('compare-divider').boundingBox();
  expect(stage && divider).toBeTruthy();
  await page.mouse.move(divider!.x + divider!.width / 2, divider!.y + divider!.height / 2);
  await page.mouse.down();
  await page.mouse.move(stage!.x + stage!.width * 0.2, divider!.y + divider!.height / 2, {
    steps: 5,
  });
  await page.mouse.up();
  await expect(result).toHaveCSS('clip-path', /80/);
  await page.screenshot({ path: 'e2e-artifacts/canvas-media-compare.png', fullPage: true });
});

test('gen slot shows the Rerun affordance on card and in lightbox', async ({ page }) => {
  await openCanvas(page);

  await expect(page.getByTestId('regenerate-open')).toBeVisible();
  await page.locator('[data-testid="output-images-grid"] img').first().click();
  await expect(
    page.getByTestId('output-lightbox').getByRole('button', { name: 'Regenerate' }),
  ).toBeVisible();
});

test('lightbox wheel-zoom + drag pan + double-click reset (P0-5)', async ({ page }) => {
  await openCanvas(page);
  await page.locator('.react-flow__node[data-id="out1"] img').first().click();
  const lightbox = page.getByTestId('output-lightbox');
  await expect(lightbox).toBeVisible();

  const stage = page.getByTestId('lightbox-stage');
  const box = (await stage.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.wheel(0, -400);
  await page.mouse.wheel(0, -400);

  const layer = page.getByTestId('lightbox-zoom-layer');
  const transform = await layer.evaluate((el) => (el as HTMLElement).style.transform);
  expect(transform).toContain('scale(');
  await expect(page.getByTestId('lightbox-zoom-readout')).toBeVisible();

  // Drag pans.
  const lb = (await layer.boundingBox())!;
  await page.mouse.move(lb.x + lb.width / 2, lb.y + lb.height / 2);
  await page.mouse.down();
  await page.mouse.move(lb.x + lb.width / 2 + 80, lb.y + lb.height / 2 + 40, { steps: 4 });
  await page.mouse.up();
  const panned = await layer.evaluate((el) => (el as HTMLElement).style.transform);
  expect(panned).not.toBe(transform);

  // Double-click resets to fit.
  await layer.dblclick();
  await expect(page.getByTestId('lightbox-zoom-readout')).toHaveCount(0);
  await page.keyboard.press('Escape');
});

test('floating toolbar on selected node — Preview opens the lightbox instantly (P2-3)', async ({ page }) => {
  await openCanvas(page);

  // Selecting the node pins the toolbar visible.
  const node = page.locator('.react-flow__node[data-id="out1"]');
  await node.click({ position: { x: 10, y: 10 } });
  const toolbar = node.getByTestId('output-node-toolbar');
  await expect(toolbar).toBeVisible();

  await toolbar.getByRole('button', { name: 'Preview' }).click();
  await expect(page.getByTestId('output-lightbox')).toBeVisible();
  await page.keyboard.press('Escape');
  await page.screenshot({ path: 'e2e-artifacts/canvas-media-toolbar.png', fullPage: true });
});

test('Download All packs a server-side zip (P2-7)', async ({ page }) => {
  await openCanvas(page);

  let zipCalled = false;
  await page.route('**/api/v1/canvases/assets/zip', (route) => {
    zipCalled = true;
    // A minimal empty-zip signature is enough to satisfy the blob save.
    return route.fulfill({
      status: 200,
      contentType: 'application/zip',
      body: Buffer.from([0x50, 0x4b, 0x05, 0x06, ...new Array(18).fill(0)]),
    });
  });

  await page.locator('[data-testid="output-images-grid"] img').first().click();
  await expect(page.getByTestId('output-lightbox')).toBeVisible();

  const download = page.waitForEvent('download').catch(() => null);
  await page.getByRole('button', { name: 'Download All' }).click();
  await expect.poll(() => zipCalled).toBe(true);
  await download;
});
