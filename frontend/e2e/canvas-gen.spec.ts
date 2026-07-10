// e2e/canvas-gen.spec.ts
// Infinite-Canvas parity Phase 2 (G4-F1): an image-kind prompt runs through
// the generation task endpoints — dispatch count×, poll to completed, and
// the durable result URLs land in per-prompt output slots on the canvas.

import { expect, test, type Page } from '@playwright/test';
import { FIXTURE_IMAGE_URL, setupStubbedSession, TEAM_ID } from './helpers/stubs';

const CANVAS = {
  id: 'c-gen',
  project_id: 'p1',
  name: 'Gen Canvas',
  kind: 'smart',
  viewport_json: { x: 0, y: 0, zoom: 1 },
  nodes_json: [
    {
      id: 'p1',
      type: 'prompt',
      position: { x: 120, y: 120 },
      data: {
        body: 'a lighthouse at dawn',
        provider_slug: '',
        agent_id: null,
        run_status: 'idle',
        resource_refs: [],
        gen: { kind: 'image', model: 'jimeng-cli-image', ratio: '16:9', count: 2 },
      },
    },
  ],
  connections_json: [],
  node_ops_json: [],
  connection_ops_json: [],
  base_updated_at: '2020-01-02T00:00:00Z',
  created_at: '2020-01-01T00:00:00Z',
  updated_at: '2020-01-02T00:00:00Z',
  created_by: null,
};

async function setupStubs(page: Page): Promise<void> {
  await page.route('**/api/v1/canvases/c-gen', (route) =>
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
  await page.route('**/api/v1/canvases/generation-models', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: [
          { name: 'jimeng-cli-image', display_name: 'Jimeng Image', type: 'image', actual_provider: 'jimeng-cli' },
        ],
      }),
    }),
  );
  await page.route('**/api/v1/canvases/c-gen/generations', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, task_ids: ['t1', 't2'] }),
    }),
  );
  // Tasks complete on first poll; result is a data-URI so <img> renders offline.
  await page.route('**/api/v1/canvases/generations/*', (route) => {
    const id = route.request().url().split('/').pop();
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: {
          phase: 'completed',
          status: 'completed',
          error_msg: null,
          metadata: { result_url: FIXTURE_IMAGE_URL, media_kind: 'image', task: id },
        },
      }),
    });
  });
}

test('image prompt Run lands both results in output slots', async ({ page }) => {
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

  await page.goto(`/team/${TEAM_ID}/canvas/c-gen`);
  await expect(page.locator('.react-flow__node')).toHaveCount(1);

  // The prompt node surfaces its generation settings.
  await expect(page.getByLabel('Prompt kind')).toHaveValue('image');
  await expect(page.getByLabel('Image count')).toHaveValue('2');

  // Cascade Run needs no selection and drives the same generation pipeline.
  await page.getByRole('button', { name: 'Cascade Run', exact: true }).click();

  // ONE output slot appears (Infinite: count N = one node, N images),
  // wired from the prompt, rendering both images as a grid.
  await expect(page.locator('.react-flow__node')).toHaveCount(2);
  await expect(page.locator('.react-flow__edge')).toHaveCount(1);
  await expect(page.locator('[data-testid="output-images-grid"] img')).toHaveCount(2);

  await page.screenshot({ path: 'e2e-artifacts/canvas-gen.png', fullPage: true });
});
