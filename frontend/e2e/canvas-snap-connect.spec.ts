// e2e/canvas-snap-connect.spec.ts
// Infinite-Canvas parity Phase 1 (G1): alt-dragging a smart node onto a
// valid target auto-creates the edge and snaps the node back. Real-browser
// drag through ReactFlow — the unit suite drives the handlers directly;
// this proves the gesture end-to-end. Full-stub harness (helpers/stubs.ts).

import { expect, test, type Page } from '@playwright/test';
import { setupStubbedSession, TEAM_ID } from './helpers/stubs';

const CANVAS = {
  id: 'c-snap',
  project_id: 'p1',
  name: 'Snap Canvas',
  kind: 'smart',
  viewport_json: { x: 0, y: 0, zoom: 1 },
  nodes_json: [
    {
      id: 'shot1',
      type: 'shot',
      position: { x: 80, y: 120 },
      data: { title: 'Opening Shot', reference_resource_ids: [], notes: '' },
    },
    {
      id: 'prompt1',
      type: 'prompt',
      position: { x: 560, y: 120 },
      data: {
        body: 'Describe the scene',
        provider_slug: null,
        agent_id: null,
        run_status: 'idle',
        resource_refs: [],
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

async function setupCanvasStubs(page: Page): Promise<void> {
  // Matches GET (load) and PUT (autosave) alike.
  await page.route('**/api/v1/canvases/c-snap', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: CANVAS }),
    }),
  );
  // PromptNodeView's @-mention search expects the ResourceSearchResponse
  // shape — the generic {success,data} catch-all is the wrong envelope.
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

test.describe('smart canvas drag-snap-connect (Phase 1 G1)', () => {
  test.beforeEach(async ({ page }) => {
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
    await setupCanvasStubs(page);
    await page.goto(`/team/${TEAM_ID}/canvas/c-snap`);
    await expect(page.locator('.react-flow__node')).toHaveCount(2);
  });

  test('alt-drag shot onto prompt wires them and snaps the shot back', async ({ page }) => {
    const shot = page.locator('.react-flow__node[data-id="shot1"]');
    const prompt = page.locator('.react-flow__node[data-id="prompt1"]');
    const shotBox = (await shot.boundingBox())!;
    const promptBox = (await prompt.boundingBox())!;
    expect(shotBox && promptBox).toBeTruthy();

    await expect(page.locator('.react-flow__edge')).toHaveCount(0);

    // Alt-drag the shot until its center sits on the prompt.
    await page.keyboard.down('Alt');
    await page.mouse.move(shotBox.x + shotBox.width / 2, shotBox.y + 20);
    await page.mouse.down();
    const targetX = promptBox.x + promptBox.width / 2;
    const targetY = promptBox.y + promptBox.height / 2;
    await page.mouse.move(targetX, targetY, { steps: 12 });

    // Mid-drag: the hovered target carries the dashed highlight.
    await expect(page.locator('.react-flow__node.mh-snap-target')).toHaveCount(1);
    await page.screenshot({ path: 'e2e-artifacts/canvas-snap-connect.png', fullPage: true });

    await page.mouse.up();
    await page.keyboard.up('Alt');

    // The edge exists and the shot snapped back to its pre-drag spot.
    await expect(page.locator('.react-flow__edge')).toHaveCount(1);
    const settled = (await shot.boundingBox())!;
    expect(Math.abs(settled.x - shotBox.x)).toBeLessThan(2);
    expect(Math.abs(settled.y - shotBox.y)).toBeLessThan(2);
  });

  test('plain drag (no Alt) moves the node without wiring', async ({ page }) => {
    const shot = page.locator('.react-flow__node[data-id="shot1"]');
    const prompt = page.locator('.react-flow__node[data-id="prompt1"]');
    const shotBox = (await shot.boundingBox())!;
    const promptBox = (await prompt.boundingBox())!;

    await page.mouse.move(shotBox.x + shotBox.width / 2, shotBox.y + 20);
    await page.mouse.down();
    await page.mouse.move(
      promptBox.x + promptBox.width / 2,
      promptBox.y + promptBox.height / 2,
      { steps: 12 },
    );
    await page.mouse.up();

    await expect(page.locator('.react-flow__edge')).toHaveCount(0);
    // And the node genuinely moved (no snap-back on a plain drag).
    const settled = (await shot.boundingBox())!;
    expect(Math.abs(settled.x - shotBox.x)).toBeGreaterThan(50);
  });
});
