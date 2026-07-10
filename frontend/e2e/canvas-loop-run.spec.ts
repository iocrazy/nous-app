// e2e/canvas-loop-run.spec.ts
// Infinite-Canvas parity Phase 1 (G3b): from-loop batch run against the
// real production build — clicking the loop node's Run button executes the
// downstream cascade rounds× with 《计数》 injected, and every round lands
// in its own output slot wired from the tail prompt.

import { expect, test, type Page } from '@playwright/test';
import { setupStubbedSession, TEAM_ID } from './helpers/stubs';

const CANVAS = {
  id: 'c-loop',
  project_id: 'p1',
  name: 'Loop Canvas',
  kind: 'smart',
  viewport_json: { x: 0, y: 0, zoom: 1 },
  nodes_json: [
    {
      id: 'loop1',
      type: 'loop',
      position: { x: 40, y: 120 },
      data: {
        mode: 'serial',
        label: 'batch',
        rounds: 2,
        round_start: 1,
        prompts: ['第《计数》张，共《总数》张'],
      },
    },
    {
      id: 'p1',
      type: 'prompt',
      position: { x: 420, y: 120 },
      data: {
        body: 'render it',
        provider_slug: null,
        agent_id: null,
        run_status: 'idle',
        resource_refs: [],
      },
    },
  ],
  connections_json: [
    { id: 'e1', source: 'loop1', target: 'p1', sourceHandle: null, targetHandle: null },
  ],
  node_ops_json: [],
  connection_ops_json: [],
  base_updated_at: '2020-01-02T00:00:00Z',
  created_at: '2020-01-01T00:00:00Z',
  updated_at: '2020-01-02T00:00:00Z',
  created_by: null,
};

async function setupStubs(page: Page): Promise<void> {
  await page.route('**/api/v1/canvases/c-loop', (route) =>
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
  // Echo runner: the result text carries the body the backend received,
  // so the assertions can prove the counter injection end-to-end.
  await page.route('**/api/v1/canvases/runs/prompts', async (route) => {
    const body = route.request().postDataJSON() as { body?: string };
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: { ok: true, text: `echo:${body?.body ?? ''}`, error: null },
      }),
    });
  });
}

test('loop Run executes rounds and stacks per-round output slots', async ({ page }) => {
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

  await page.goto(`/team/${TEAM_ID}/canvas/c-loop`);
  await expect(page.locator('.react-flow__node')).toHaveCount(2);

  await page.getByLabel('Run loop').click();

  // Two rounds → two output slots appear, wired from the prompt.
  await expect(page.locator('.react-flow__node')).toHaveCount(4);
  await expect(page.locator('.react-flow__edge')).toHaveCount(3);

  // Counter injection proven end-to-end via the echo runner.
  await expect(page.getByText('Run 1: echo:第1张，共2张', { exact: false })).toBeVisible();
  await expect(page.getByText('Run 2: echo:第2张，共2张', { exact: false })).toBeVisible();

  await page.screenshot({ path: 'e2e-artifacts/canvas-loop-run.png', fullPage: true });
});
