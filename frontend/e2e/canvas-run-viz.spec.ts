// e2e/canvas-run-viz.spec.ts
// Infinite-Canvas parity Phase 1 (G2): run-state edge colouring against the
// real production build — edges pick up their adjacent prompt's run_status
// (wait/active/done) so a cascade visibly flows along the wires.

import { expect, test, type Page } from '@playwright/test';
import { setupStubbedSession, TEAM_ID } from './helpers/stubs';

function prompt(id: string, x: number, y: number, runStatus: string, body: string) {
  return {
    id,
    type: 'prompt',
    position: { x, y },
    data: {
      body,
      provider_slug: null,
      agent_id: null,
      run_status: runStatus,
      resource_refs: [],
    },
  };
}

const CANVAS = {
  id: 'c-runviz',
  project_id: 'p1',
  name: 'Run Viz Canvas',
  kind: 'smart',
  viewport_json: { x: 0, y: 0, zoom: 1 },
  nodes_json: [
    { id: 'shot1', type: 'shot', position: { x: 40, y: 60 }, data: { title: 'Shot', reference_resource_ids: [], notes: '' } },
    prompt('p-done', 400, 40, 'succeeded', 'Done prompt'),
    prompt('p-active', 400, 260, 'running', 'Active prompt'),
    prompt('p-wait', 400, 480, 'queued', 'Waiting prompt'),
    { id: 'out1', type: 'output', position: { x: 800, y: 40 }, data: { kind: 'text', resource_id: null, preview_text: 'result', preview_url: null, crop_region: null } },
  ],
  connections_json: [
    { id: 'e-done', source: 'shot1', target: 'p-done', sourceHandle: null, targetHandle: null },
    { id: 'e-active', source: 'shot1', target: 'p-active', sourceHandle: null, targetHandle: null },
    { id: 'e-wait', source: 'shot1', target: 'p-wait', sourceHandle: null, targetHandle: null },
    { id: 'e-out', source: 'p-done', target: 'out1', sourceHandle: null, targetHandle: null },
  ],
  node_ops_json: [],
  connection_ops_json: [],
  base_updated_at: '2020-01-02T00:00:00Z',
  created_at: '2020-01-01T00:00:00Z',
  updated_at: '2020-01-02T00:00:00Z',
  created_by: null,
};

async function setupStubs(page: Page): Promise<void> {
  await page.route('**/api/v1/canvases/c-runviz', (route) =>
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
}

test('edges carry run-state classes matching their prompt status', async ({ page }) => {
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

  await page.goto(`/team/${TEAM_ID}/canvas/c-runviz`);
  await expect(page.locator('.react-flow__node')).toHaveCount(5);

  await expect(page.locator('.react-flow__edge.mh-edge-done')).toHaveCount(2);
  await expect(page.locator('.react-flow__edge.mh-edge-active')).toHaveCount(1);
  await expect(page.locator('.react-flow__edge.mh-edge-wait')).toHaveCount(1);

  // The active wire animates (dash-flow) with the accent stroke.
  const activePath = page.locator('.mh-edge-active .react-flow__edge-path').first();
  await expect(activePath).toHaveCSS('animation-name', 'canvas-edge-flow');

  await page.screenshot({ path: 'e2e-artifacts/canvas-run-viz.png', fullPage: true });
});
