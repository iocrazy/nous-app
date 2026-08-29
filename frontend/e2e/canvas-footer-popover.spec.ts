// Footer popovers in a real browser.
//
// The report was "sometimes it vanishes when I move up". That is a pointer-PATH
// problem: jsdom has no pointer path, so a unit test can only assert the shape
// that causes it (a margin gap). Here we actually move the mouse from the pill
// to the popover and check it survives the trip.

import { expect, test, type Page } from '@playwright/test';
import { setupStubbedSession, TEAM_ID } from './helpers/stubs';

const CANVAS = {
  id: 'c-foot',
  project_id: 'p1',
  name: 'Footer',
  kind: 'smart',
  viewport_json: { x: 0, y: 0, zoom: 1 },
  nodes_json: [
    {
      id: 'p1',
      type: 'prompt',
      position: { x: 320, y: 220 },
      data: {
        body: 'a wide shot',
        provider_slug: null,
        agent_id: null,
        run_status: 'idle',
        resource_refs: [],
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
  await page.route('**/api/v1/canvases/c-foot', (route) =>
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
  await page.goto(`/team/${TEAM_ID}/canvas/c-foot`);
  await expect(page.getByTestId('pill-count')).toBeVisible();
}

test('the popover survives the pointer travelling from the pill onto it', async ({ page }) => {
  await openCanvas(page);
  const pill = page.getByTestId('pill-count');
  await pill.click();
  await expect(page.getByTestId('count-option').first()).toBeVisible();

  const pillBox = await pill.boundingBox();
  const optionBox = await page.getByTestId('count-option').first().boundingBox();
  if (!pillBox || !optionBox) throw new Error('missing box');

  // Walk the real path: pill centre → option centre, in small steps. Any dead
  // space along the way fires mouseleave and the menu closes under the cursor.
  await page.mouse.move(pillBox.x + pillBox.width / 2, pillBox.y + pillBox.height / 2);
  await page.mouse.move(optionBox.x + optionBox.width / 2, optionBox.y + optionBox.height / 2, {
    steps: 25,
  });

  await expect(
    page.getByTestId('count-option').first(),
    'popover closed while the pointer was moving onto it',
  ).toBeVisible();

  // And it is still usable at the end of that trip.
  await page.getByTestId('count-option').nth(3).click();
  await expect(page.getByTestId('count-option')).toHaveCount(0);
});

test('the popover opens below the pills, not over the prompt', async ({ page }) => {
  await openCanvas(page);
  const pill = page.getByTestId('pill-count');
  await pill.click();

  const pillBox = await pill.boundingBox();
  const optionBox = await page.getByTestId('count-option').first().boundingBox();
  if (!pillBox || !optionBox) throw new Error('missing box');

  expect(
    optionBox.y,
    `popover top (${optionBox.y}) is above the pill (${pillBox.y}) — it still opens upward over the prompt`,
  ).toBeGreaterThan(pillBox.y);
});

test('a HOVER-opened popover follows the pointer away (IC parity)', async ({ page }) => {
  await openCanvas(page);
  // Hover only — no click, so nothing is pinned.
  await page.getByTestId('pill-count').hover();
  await expect(page.getByTestId('count-option').first()).toBeVisible();

  await page.mouse.move(40, 40, { steps: 10 });
  await expect(page.getByTestId('count-option')).toHaveCount(0);
});

test('a CLICKED popover stays put, and a click outside dismisses it', async ({ page }) => {
  await openCanvas(page);
  await page.getByTestId('pill-count').click();
  await expect(page.getByTestId('count-option').first()).toBeVisible();

  // Deliberate open: moving the pointer away must not take it with them.
  await page.mouse.move(40, 40, { steps: 10 });
  await expect(
    page.getByTestId('count-option').first(),
    'a clicked popover vanished when the pointer left',
  ).toBeVisible();

  await page.mouse.click(40, 40);
  await expect(page.getByTestId('count-option')).toHaveCount(0);
});
