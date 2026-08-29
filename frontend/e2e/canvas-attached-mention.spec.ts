// @-mention + image chips in a real browser.
//
// jsdom cannot answer the two questions that actually decide this feature:
// whether React Flow lets the popover receive clicks at all (its drag handler
// listens for mousedown on the node and stops propagation — a synthetic
// dispatch in a unit test sails past that and proves nothing), and whether a
// real IME survives the store round-trip.
//
// Interop probes carry a NEGATIVE CONTROL, so "the canvas did not move" can
// never pass merely because a selector missed.

import { expect, test, type Page } from '@playwright/test';
import { setupStubbedSession, TEAM_ID } from './helpers/stubs';

const IMG_A = '/api/v1/generated-media/901/cover';
const IMG_B = '/api/v1/generated-media/902/cover';

const CANVAS = {
  id: 'c-att',
  project_id: 'p1',
  name: 'Attached Mention',
  kind: 'smart',
  viewport_json: { x: 0, y: 0, zoom: 1 },
  nodes_json: [
    {
      id: 'm1',
      type: 'media',
      position: { x: 260, y: 160 },
      data: {
        title: 'Media',
        items: [
          { url: IMG_A, kind: 'image', name: 'a.png' },
          { url: IMG_B, kind: 'image', name: 'b.png' },
        ],
      },
    },
    {
      id: 'p1',
      type: 'prompt',
      position: { x: 700, y: 160 },
      data: {
        body: '',
        provider_slug: null,
        agent_id: null,
        run_status: 'idle',
        resource_refs: [],
      },
    },
  ],
  connections_json: [
    { id: 'e1', source: 'm1', target: 'p1', sourceHandle: null, targetHandle: null },
  ],
  node_ops_json: [],
  connection_ops_json: [],
  base_updated_at: '2020-01-02T00:00:00Z',
  created_at: '2020-01-01T00:00:00Z',
  updated_at: '2020-01-02T00:00:00Z',
  created_by: null,
};

// 1×1 PNG so thumbnails actually load instead of rendering broken.
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64',
);

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
  await page.route('**/api/v1/canvases/c-att', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: CANVAS }),
    }),
  );
  await page.route('**/api/v1/generated-media/**', (route) =>
    route.fulfill({ status: 200, contentType: 'image/png', body: PNG }),
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
  await page.goto(`/team/${TEAM_ID}/canvas/c-att`);
  await expect(page.locator('.react-flow__node[data-id="m1"]')).toBeVisible();
}

/** Select the media node so its attached composer panel appears. */
async function openAttachedPanel(page: Page) {
  await page.locator('.react-flow__node[data-id="m1"]').click({ position: { x: 40, y: 8 } });
  const panel = page.getByTestId('attached-composer');
  await expect(panel).toBeVisible();
  return panel;
}

test('attached panel: @ offers the node images and picking one leaves a chip', async ({
  page,
}) => {
  await openCanvas(page);
  await openAttachedPanel(page);

  const editor = page.getByTestId('attached-prompt-editor');
  await editor.click();
  await page.keyboard.type('brighten @');

  const options = page.getByTestId('mention-image-option');
  await expect(options).toHaveCount(2);
  await expect(options.first()).toContainText('Image 1');

  // A REAL click — this is the step a synthetic dispatch cannot vouch for.
  await options.nth(1).click();

  const chip = page.getByTestId('prompt-image-chip');
  await expect(chip).toBeVisible();
  await expect(chip).toContainText('Image 2');
  const loaded = await chip
    .locator('img')
    .evaluate((el) => (el as HTMLImageElement).naturalWidth > 0);
  expect(loaded, 'chip thumbnail did not load').toBe(true);
});

test('attached panel: the chip can be removed again', async ({ page }) => {
  await openCanvas(page);
  await openAttachedPanel(page);
  const editor = page.getByTestId('attached-prompt-editor');
  await editor.click();
  await page.keyboard.type('@');
  await page.getByTestId('mention-image-option').first().click();
  await expect(page.getByTestId('prompt-image-chip')).toBeVisible();

  await page.getByRole('button', { name: /remove image 1/i }).click();
  await expect(page.getByTestId('prompt-image-chip')).toHaveCount(0);
});

test('prompt node: @ input tab inserts a chip, and it is clickable for real', async ({
  page,
}) => {
  await openCanvas(page);
  const editor = page.getByTestId('prompt-body-editor');
  await editor.click();
  await page.keyboard.type('@');

  const option = page.getByTestId('mention-input-option').first();
  await expect(option).toBeVisible();
  await option.click();

  const chip = page.getByTestId('prompt-image-chip');
  await expect(chip).toBeVisible();
  await expect(chip).toContainText('Image 1');
});

test('selecting text in the prompt does not drag the node', async ({ page }) => {
  await openCanvas(page);
  const editor = page.getByTestId('prompt-body-editor');
  await editor.click();
  await page.keyboard.type('some text worth selecting');

  const node = page.locator('.react-flow__node[data-id="p1"]');
  const before = await node.evaluate((el) => (el as HTMLElement).style.transform);
  const box = await editor.boundingBox();
  if (!box) throw new Error('editor has no box');

  await page.mouse.move(box.x + 8, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width - 8, box.y + box.height / 2, { steps: 10 });
  await page.mouse.up();
  expect(
    await node.evaluate((el) => (el as HTMLElement).style.transform),
    'node moved while selecting text',
  ).toBe(before);

  // NEGATIVE CONTROL — the same drag on the node header MUST move it.
  const nb = await node.boundingBox();
  if (!nb) throw new Error('node has no box');
  await page.mouse.move(nb.x + nb.width / 2, nb.y + 6);
  await page.mouse.down();
  await page.mouse.move(nb.x + nb.width / 2 + 130, nb.y + 90, { steps: 10 });
  await page.mouse.up();
  expect(
    await node.evaluate((el) => (el as HTMLElement).style.transform),
    'CONTROL FAILED: the node never moves, so this test proves nothing',
  ).not.toBe(before);
});

test('Chinese IME input survives the store round-trip', async ({ page }) => {
  await openCanvas(page);
  const editor = page.getByTestId('prompt-body-editor');
  await editor.click();

  await editor.evaluate(async (el) => {
    const target = el as HTMLElement;
    const fire = (type: string, data: string) =>
      target.dispatchEvent(
        new CompositionEvent(type, { data, bubbles: true, cancelable: true }),
      );
    fire('compositionstart', '');
    await new Promise((r) => setTimeout(r, 30));
    fire('compositionupdate', 'ni');
    await new Promise((r) => setTimeout(r, 30));
    fire('compositionupdate', 'nihao');
    await new Promise((r) => setTimeout(r, 50));
    const sel = window.getSelection();
    const range = sel?.rangeCount ? sel.getRangeAt(0) : null;
    if (range) {
      range.insertNode(document.createTextNode('你好世界'));
      range.collapse(false);
    }
    target.dispatchEvent(
      new InputEvent('input', {
        data: '你好世界',
        inputType: 'insertCompositionText',
        bubbles: true,
      }),
    );
    fire('compositionend', '你好世界');
  });

  await expect(editor).toContainText('你好世界');
  await page.waitForTimeout(500); // let the store echo land
  await expect(editor).toContainText('你好世界');
});

test('the popover opens below the box and the chip thumbnail is a real image', async ({
  page,
}) => {
  // Two things a unit test cannot settle: where the popover actually lands
  // relative to the box, and whether the chip's <img> genuinely decoded.
  await openCanvas(page);
  await openAttachedPanel(page);
  const editor = page.getByTestId('attached-prompt-editor');
  await editor.click();
  await page.keyboard.type('@');

  const grid = page.getByTestId('mention-image-grid');
  await expect(grid).toBeVisible();

  const editorBox = await editor.boundingBox();
  const gridBox = await grid.boundingBox();
  if (!editorBox || !gridBox) throw new Error('missing box');
  // IC opens downward. Anchored above, it covered the input-image row.
  expect(
    gridBox.y,
    `popover top (${gridBox.y}) is above the editor top (${editorBox.y}) — it opened upward`,
  ).toBeGreaterThan(editorBox.y);

  await page.getByTestId('mention-image-option').first().click();
  const chip = page.getByTestId('prompt-image-chip');
  await expect(chip).toBeVisible();
  const decoded = await chip.locator('img').evaluate(
    (el) => (el as HTMLImageElement).naturalWidth > 0,
  );
  expect(decoded, 'chip thumbnail did not decode').toBe(true);
});
