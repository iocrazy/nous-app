// e2e/canvas-media.spec.ts
// Infinite-Canvas parity Phase 3 (G7) against the real production build:
// clicking an output image opens the fullscreen lightbox; arrows navigate;
// a slot with history offers the Compare slider; gen slots show Rerun.

import { expect, test, type Page } from '@playwright/test';
import { setupStubbedSession, TEAM_ID } from './helpers/stubs';

// 1×1 transparent PNG — same-origin data URLs render instantly and avoid
// network flakiness for image loads.
const PNG =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==';

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
          { url: PNG, kind: 'image', name: 'two.png' },
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
        images: [{ url: PNG, kind: 'image', name: 'old.png' }],
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
  await expect(page.getByTestId('lightbox-counter')).toHaveText('1 / 2');

  await page.getByRole('button', { name: 'Next' }).click();
  await expect(page.getByTestId('lightbox-counter')).toHaveText('2 / 2');

  await page.screenshot({ path: 'e2e-artifacts/canvas-media-lightbox.png', fullPage: true });
  await page.keyboard.press('Escape');
  await expect(lightbox).toHaveCount(0);
});

test('history-backed slot offers Compare with a working slider', async ({ page }) => {
  await openCanvas(page);

  await page.locator('[data-testid="output-images-grid"] img').first().click();
  await page.getByRole('button', { name: 'Compare' }).click();

  const result = page.getByTestId('compare-result');
  await expect(result).toBeVisible();
  await page.getByRole('slider').fill('20');
  await expect(result).toHaveCSS('clip-path', /80%/);
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
