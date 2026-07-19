// Beats M2 — the Arrangement timeline: card geometry from the wire data, a real
// step-by-step pointer drag that must PATCH the snapped start_sec, and a zoom
// button that must rescale the card width.
//
// The drag is deliberately a manual mouse.down/move/up (NOT locator.dragTo):
// arrangement drag is bookkept by pointermove and only writes on pointer-up, so
// a jump-to-destination drag lands too few moves to register (same lesson as the
// scene-drag spec / #1389). Zoom is seeded to a known px-per-second so the pixel
// assertions are deterministic.

import { expect, test } from '@playwright/test';
import {
  SCRIPT_ID,
  SCRIPT_URL,
  setupScriptStubs,
  wireScene,
  type WireElement,
} from './helpers/script-stubs';

/** 15-digit, 53-bit-safe id (survives JSON parse intact — see script-stubs). */
const BEAT_ID = 323456789200001;
/** Seeded zoom: 6 px per timeline second → left/width math is exact. */
const PX_PER_SEC = 6;

const els: WireElement[] = [
  { id: 'el_2000001', type: 'action', text: 'A cold open.' },
];

/** A beat arranged at 20s for 40s → total timeline 60s (seconds grid, 5s snap). */
const arrangedBeat = () => ({
  id: BEAT_ID,
  script_id: Number(SCRIPT_ID),
  title: 'Opening Image',
  summary: null,
  scene_ids: [],
  sort_order: 1000,
  start_sec: 20,
  duration_sec: 40,
  beat_role: null,
  color: '#b8a9a0',
});

async function setupBeats(page: import('@playwright/test').Page) {
  await setupScriptStubs(page, {
    scenes: [wireScene({ id: 323456789100000, sortOrder: 1, location: 'ROOFTOP', elements: els })],
    format: 'hollywood',
  });

  // Deterministic sub-view + zoom (both bare-string localStorage, like the app).
  await page.addInitScript(
    ([viewKey, zoomKey, zoom]) => {
      try {
        localStorage.setItem('language', 'en');
        localStorage.setItem(viewKey as string, 'arrangement');
        localStorage.setItem(zoomKey as string, zoom as string);
      } catch {
        /* localStorage unavailable */
      }
    },
    [`editor.beatsView.${SCRIPT_ID}`, `editor.beatsZoom.${SCRIPT_ID}`, String(PX_PER_SEC)] as const,
  );

  await page.route(`**/api/v1/scripts/${SCRIPT_ID}/beats`, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: [arrangedBeat()] }),
    }),
  );
}

async function openArrangement(page: import('@playwright/test').Page) {
  await page.goto(SCRIPT_URL);
  await page.waitForSelector('[data-testid="scene-block"]', { timeout: 15_000 });
  await page.locator('.mh-rail-module', { hasText: 'Beats' }).click();
  await page.waitForSelector('[data-testid="arr-card"]', { timeout: 10_000 });
}

test('renders an arranged card at start×zoom with width duration×zoom', async ({ page }) => {
  await setupBeats(page);
  await openArrangement(page);

  const card = page.locator('[data-testid="arr-card"]');
  const box = await card.boundingBox();
  if (!box) throw new Error('arr-card has no box');
  // width = 40s × 6 = 240 (± border). left offset relative to lanes = 20s × 6 = 120.
  expect(box.width).toBeGreaterThan(232);
  expect(box.width).toBeLessThan(250);

  const lanes = await page.locator('[data-testid="arr-lanes"]').boundingBox();
  if (!lanes) throw new Error('arr-lanes has no box');
  expect(box.x - lanes.x).toBeGreaterThan(114);
  expect(box.x - lanes.x).toBeLessThan(126);
});

test('dragging a card body PATCHes the snapped start_sec', async ({ page }) => {
  await setupBeats(page);

  let patchBody: Record<string, unknown> | null = null;
  await page.route(`**/api/v1/beats/${BEAT_ID}`, async (route) => {
    if (route.request().method() === 'PATCH') {
      patchBody = JSON.parse(route.request().postData() || '{}');
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ success: true, data: { ...arrangedBeat(), ...patchBody } }),
      });
    } else {
      await route.fallback();
    }
  });

  await openArrangement(page);

  const box = await page.locator('[data-testid="arr-card"]').boundingBox();
  if (!box) throw new Error('arr-card has no box');
  // Grab the card body (left third, clear of the right-edge resize handle).
  const cx = box.x + 30;
  const cy = box.y + box.height / 2;
  await page.mouse.move(cx, cy);
  await page.mouse.down();
  await page.mouse.move(cx + 30, cy, { steps: 6 });
  await page.mouse.move(cx + 60, cy, { steps: 6 }); // +60px = +10s → 20+10 = 30 (5s grid)
  await page.mouse.up();

  await expect.poll(() => patchBody).not.toBeNull();
  expect(patchBody).toEqual({ start_sec: 30 });
});

test('Zoom in widens the card (px-per-second grows)', async ({ page }) => {
  await setupBeats(page);
  await openArrangement(page);

  const card = page.locator('[data-testid="arr-card"]');
  const before = (await card.boundingBox())?.width ?? 0;

  await page.locator('[data-testid="arr-zoom-in"]').click();

  await expect
    .poll(async () => (await card.boundingBox())?.width ?? 0)
    .toBeGreaterThan(before + 30); // 40s × (6 → 7.5) = 240 → 300
});
