// The output slot must appear the moment a run is dispatched — from EVERY
// entry point, not just the composer's Run.
//
// The whole point is what the canvas looks like WHILE the run is in flight, so
// the backend stub is pinned to a never-finishing state. A stub that returns
// results immediately would let a "slot appears at the end" implementation
// pass this test.

import { expect, test, type Page } from '@playwright/test';
import { setupStubbedSession, TEAM_ID } from './helpers/stubs';

const CANVAS = {
  id: 'c-slot',
  project_id: 'p1',
  name: 'Dispatch',
  kind: 'smart',
  viewport_json: { x: 0, y: 0, zoom: 1 },
  nodes_json: [
    {
      id: 'p1',
      type: 'prompt',
      position: { x: 300, y: 240 },
      data: {
        body: 'a cat on a roof',
        provider_slug: null,
        agent_id: null,
        run_status: 'idle',
        resource_refs: [],
        gen: { kind: 'image', model: '', ratio: '1:1', count: 2 },
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
    } catch {
      /* unavailable */
    }
  });
  await setupStubbedSession(page);
  await page.route('**/api/v1/canvases/c-slot', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: CANVAS }),
    }),
  );
  await page.route('**/api/v1/resources/search**', (route) =>
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
  // Dispatch accepted, task ids handed back. Shapes copied from
  // canvasGenerationService (`{success, task_ids}` / `{success, data:
  // GenerationTask}`) rather than invented — a mock that does not match the
  // real wire shape tests nothing.
  await page.route('**/api/v1/canvases/*/generations', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, task_ids: ['slot-t1', 'slot-t2'] }),
    }),
  );
  // …and never finishing: `phase` stays off the terminal list. This is what
  // keeps the assertions honest — a slot created only on completion would
  // never show up here.
  await page.route('**/api/v1/canvases/generations/*', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: { phase: 'in_progress', status: 'running', error_msg: null, metadata: {} },
      }),
    }),
  );
  await page.goto(`/team/${TEAM_ID}/canvas/c-slot`);
  await expect(page.locator('.react-flow__node[data-id="p1"]')).toBeVisible();
}

test('running from the prompt node puts a pending slot to its right, wired up', async ({
  page,
}) => {
  await openCanvas(page);
  const before = await page.locator('.react-flow__node').count();

  await page.getByTestId('prompt-node-run').click();

  // A new node, while the run is still in flight.
  await expect(page.locator('.react-flow__node')).toHaveCount(before + 1, { timeout: 8000 });

  const prompt = await page.locator('.react-flow__node[data-id="p1"]').boundingBox();
  const slot = await page
    .locator('.react-flow__node')
    .filter({ hasNot: page.locator('[data-id="p1"]') })
    .last()
    .boundingBox();
  if (!prompt || !slot) throw new Error('missing box');

  expect(slot.x, 'the new node is not to the right of the prompt').toBeGreaterThan(prompt.x);

  // And an edge now leaves the prompt — the run has a visible direction.
  await expect(page.locator('.react-flow__edge')).toHaveCount(1);

  // Still running: this is the in-flight state, not the finished one.
  await expect(page.locator('.react-flow__node[data-id="p1"]')).toContainText(/running/i);
});

test('the slot does not wait for results — it is there while pending', async ({ page }) => {
  await openCanvas(page);
  await page.getByTestId('prompt-node-run').click();
  await expect(page.locator('.react-flow__node')).toHaveCount(2, { timeout: 8000 });

  // Give the poller several rounds; the stub never completes, so anything
  // that only appears on completion would still be missing here.
  await page.waitForTimeout(2500);
  await expect(
    page.locator('.react-flow__node'),
    'the pending slot vanished while the run was still going',
  ).toHaveCount(2);
});
