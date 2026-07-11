// e2e/canvas-timeline.spec.ts
// Timeline director (G8) against the real production build: build segments
// on the node, Run dispatches ONE film task, poll completes, the durable
// /stream lands in a video output slot wired to the timeline.

import { expect, test, type Page } from '@playwright/test';
import { setupStubbedSession, TEAM_ID } from './helpers/stubs';

const CANVAS = {
  id: 'c-tl',
  project_id: 'p1',
  name: 'Timeline Canvas',
  kind: 'smart',
  viewport_json: { x: 0, y: 0, zoom: 1 },
  nodes_json: [
    {
      id: 'tl1',
      type: 'timeline',
      position: { x: 80, y: 80 },
      data: {
        segments: [
          { id: 's1', prompt: 'a lighthouse at dawn', seconds: 5 },
          { id: 's2', prompt: 'waves crash on rocks', seconds: 3 },
        ],
        model: '',
        aspect: '16:9',
        run_status: 'idle',
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

async function openCanvas(page: Page): Promise<void> {
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.addInitScript(() => {
    try {
      localStorage.setItem('language', 'en');
      localStorage.setItem('mediahub.theme', 'dark');
    } catch { /* unavailable */ }
  });
  await setupStubbedSession(page);
  await page.route('**/api/v1/canvases/c-tl', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true, data: CANVAS }) }),
  );
  await page.route('**/api/v1/resources/search*', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ results: [], counts: { all: 0, video: 0, image: 0, doc: 0, audio: 0, pdf: 0 }, next_cursor: null }) }),
  );
  await page.goto(`/team/${TEAM_ID}/canvas/c-tl`);
  await expect(page.locator('.react-flow__node')).toHaveCount(1);
}

test('segments edit + Run lands the film in a video slot', async ({ page }) => {
  await openCanvas(page);

  let dispatched: unknown = null;
  await page.route('**/api/v1/canvases/c-tl/timeline-runs', (route) => {
    dispatched = JSON.parse(route.request().postData() ?? '{}');
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true, data: { task_id: 'wf-1' } }) });
  });
  await page.route('**/api/v1/canvases/generations/wf-1', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true, data: { phase: 'completed', metadata: { result_url: '/api/v1/generated-media/9/stream' } } }) }),
  );

  // Strip renders both segments; add a third and give it a prompt.
  await expect(page.getByTestId('timeline-strip').locator('button[data-testid^="timeline-seg-"]')).toHaveCount(2);
  await page.getByRole('button', { name: 'Add segment' }).click();
  await page.getByTestId('timeline-strip').locator('button[data-testid^="timeline-seg-"]').nth(2).click();
  await page.getByPlaceholder('Segment prompt…').fill('sunset fades to black');
  await expect(page.getByText(/13s total · 3 segments/)).toBeVisible();

  await page.getByTestId('smart-timeline-node').getByRole('button', { name: 'Run' }).click();
  await expect(page.locator('.react-flow__node')).toHaveCount(2);
  const film = page.locator('video');
  await expect(film).toHaveAttribute('src', /generated-media\/9\/stream/);

  expect((dispatched as { segments: unknown[] }).segments).toHaveLength(3);
  await page.screenshot({ path: 'e2e-artifacts/canvas-timeline.png', fullPage: true });
});
