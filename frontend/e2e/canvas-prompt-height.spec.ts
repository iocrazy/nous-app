// The prompt body must only change height when the user DRAGS its resizer.
//
// Reported: "why does double-clicking the prompt panel change its size — I
// click a few times and it shrinks." Height is persisted from a `mouseup` on
// the body container, and every click inside it ends with a mouseup, so
// ordinary clicking (and double-clicking to select a word) writes height too.
//
// Reproduction first: click without dragging and watch the number move.

import { expect, test, type Page } from '@playwright/test';
import { setupStubbedSession, TEAM_ID } from './helpers/stubs';

const CANVAS = {
  id: 'c-h',
  project_id: 'p1',
  name: 'Height',
  kind: 'smart',
  // The canvas is zoomed — this is the condition that was missing. React
  // Flow scales the surface with a CSS transform, and getBoundingClientRect
  // reports SCALED pixels while style.height is written unscaled.
  viewport_json: { x: 0, y: 0, zoom: 0.75 },
  nodes_json: [
    {
      id: 'p1',
      type: 'prompt',
      position: { x: 260, y: 200 },
      data: {
        body: 'a cat on a roof',
        provider_slug: null,
        agent_id: null,
        run_status: 'idle',
        resource_refs: [],
        // Reproduce the reported node: already dragged taller (body_h set),
        // holding image chips and an input row — that is the shape the user
        // was clicking on, and it is not the same as a fresh default node.
        body_h: 400,
        image_refs: [
          { url: '/api/v1/generated-media/1/cover', alias: 'Image 1', kind: 'image' },
          { url: '/api/v1/generated-media/2/cover', alias: 'Image 2', kind: 'image' },
        ],
        gen: { kind: 'image', model: '', ratio: '1:1', count: 1 },
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
  await page.route('**/api/v1/canvases/c-h', (route) =>
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
  await page.goto(`/team/${TEAM_ID}/canvas/c-h`);
  await expect(page.getByTestId('prompt-body-editor')).toBeVisible();
}

/** The PERSISTED body height (what `body_h` writes into the inline style).
 *  Asserting on the node's rendered height instead would also catch the
 *  harmless reflow that focusing the editor causes — the thing that must not
 *  move is the stored value. */
const storedHeight = (page: Page) =>
  page
    .getByTestId('prompt-body-resizer')
    .evaluate((el) => (el as HTMLElement).style.height);

test('clicking in the prompt body does not resize the node', async ({ page }) => {
  await openCanvas(page);
  const editor = page.getByTestId('prompt-body-editor');
  const before = await storedHeight(page);

  for (let i = 0; i < 5; i += 1) {
    await editor.click();
  }

  expect(
    await storedHeight(page),
    'the persisted height changed from plain clicks — nothing was dragged',
  ).toBe(before);
});

test('double-clicking to select a word does not resize the node', async ({ page }) => {
  await openCanvas(page);
  const editor = page.getByTestId('prompt-body-editor');
  const before = await storedHeight(page);

  await editor.dblclick();
  await editor.dblclick();

  expect(await storedHeight(page), 'double-click changed the persisted height').toBe(before);
});

// NOT covered here: dragging the resize grip itself.
//
// Driving the browser's native `resize` handle through a CSS-transformed
// (zoomed) surface is unreliable — measured 1 failure in 3 with the pointer
// aimed at the same spot, because the draggable corner is smaller than the
// painted one and shrinks with the zoom. A test that red-flags one run in
// three is worse than none: it trains everyone to re-run instead of read.
//
// That path is covered by PromptNodeView.chain.test.tsx ("persists a dragged
// body height"), which drives the real sequence — a press ON THE GRIP plus a
// height change across it — and by a manual check on the real canvas.
