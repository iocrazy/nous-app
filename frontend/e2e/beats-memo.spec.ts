// Beats M4 — the timeline memo-pin rail. Hovering the second ruler reveals a
// "+" that opens a quick-capture card; publishing POSTs an inspiration note
// carrying the timeline anchor. An existing anchored note renders as a draggable
// pin; a real step-by-step drag PATCHes the snapped anchor_sec.
//
// Drag is a manual mouse.down/move/up (NOT locator.dragTo): the pin is bookkept
// by pointermove and only writes on pointer-up, so a jump-to-destination drag
// registers too few moves (same lesson as the arrangement / scene-drag specs).

import { expect, test } from '@playwright/test';
import {
  SCRIPT_ID,
  SCRIPT_URL,
  setupScriptStubs,
  wireScene,
  type WireElement,
} from './helpers/script-stubs';

/** Seeded zoom: 6 px per timeline second → left/width math is exact. */
const PX_PER_SEC = 6;
/** String ids: the backend serializes note bigints via coerce_numbers_to_str. */
const NOTE_ID = '900000000000001';

const els: WireElement[] = [{ id: 'el_2000001', type: 'action', text: 'A cold open.' }];

/** A beat arranged 0–120s → 120s timeline (minutes grid, 5s snap). */
const arrangedBeat = () => ({
  id: 323456789200001,
  script_id: Number(SCRIPT_ID),
  title: 'Opening Image',
  summary: null,
  scene_ids: [],
  sort_order: 1000,
  start_sec: 0,
  duration_sec: 120,
  beat_role: null,
  color: '#b8a9a0',
});

function anchoredNote(anchorSec: number) {
  return {
    id: NOTE_ID,
    content_md: 'a captured idea',
    tags: [],
    ref_hotspot: null,
    pinned: false,
    note_date: '2026-07-19',
    anchor_script_id: SCRIPT_ID,
    anchor_sec: anchorSec,
    created_at: '2026-07-19T00:00:00Z',
    updated_at: '2026-07-19T00:00:00Z',
    attachments: [],
  };
}

async function setupBeats(
  page: import('@playwright/test').Page,
  notes: ReturnType<typeof anchoredNote>[],
) {
  await setupScriptStubs(page, {
    scenes: [wireScene({ id: 323456789100000, sortOrder: 1, location: 'ROOFTOP', elements: els })],
    format: 'hollywood',
  });

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

  // Memo rail list — anchored notes for this script.
  await page.route(`**/api/v1/inspiration/notes?*anchor_script_id*`, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(notes) }),
  );
}

async function openArrangement(page: import('@playwright/test').Page) {
  await page.goto(SCRIPT_URL);
  await page.waitForSelector('[data-testid="scene-block"]', { timeout: 15_000 });
  await page.locator('.mh-rail-module', { hasText: 'Beats' }).click();
  // The beats chunk lazy-loads on first hit (cold cache); wait for the timeline
  // shell before the rail so a slow first chunk fetch doesn't flake the rail wait.
  await page.waitForSelector('[data-testid="arr-viewport"]', { timeout: 15_000 });
  await page.waitForSelector('[data-testid="memo-rail"]', { timeout: 15_000 });
}

test('hovering the rail reveals the "+" and publish POSTs an anchored note', async ({ page }) => {
  await setupBeats(page, []);

  let postBody: Record<string, unknown> | null = null;
  await page.route(`**/api/v1/inspiration/notes`, async (route) => {
    if (route.request().method() === 'POST') {
      postBody = JSON.parse(route.request().postData() || '{}');
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({ ...anchoredNote(30), content_md: postBody.content_md }),
      });
    } else {
      await route.fallback();
    }
  });

  await openArrangement(page);

  // Hover shows the "+" hint, then click the rail at 30s (30 × 6 = 180px from
  // the rail's left edge) to open the create card at that instant.
  const hitzone = page.locator('[data-testid="memo-hitzone"]');
  await hitzone.hover({ position: { x: 180, y: 8 } });
  await expect(page.locator('[data-testid="memo-add"]')).toBeVisible();
  await hitzone.click({ position: { x: 180, y: 8 } });

  await page.locator('[data-testid="memo-quick-body"]').fill('timeline thought');
  await page.locator('[data-testid="memo-quick-publish"]').click();

  await expect.poll(() => postBody).not.toBeNull();
  expect(postBody).toMatchObject({
    content_md: 'timeline thought',
    anchor_script_id: SCRIPT_ID,
    anchor_sec: 30,
  });
});

test('renders a pin for an anchored note', async ({ page }) => {
  await setupBeats(page, [anchoredNote(60)]);
  await openArrangement(page);
  await expect(page.locator('[data-testid="memo-dot"]')).toHaveCount(1);
  await expect(page.locator('[data-testid="memo-card"]')).toHaveCount(1);
});

test('dragging a pin PATCHes the snapped anchor_sec', async ({ page }) => {
  await setupBeats(page, [anchoredNote(20)]);

  let patchBody: Record<string, unknown> | null = null;
  await page.route(`**/api/v1/inspiration/notes/${NOTE_ID}`, async (route) => {
    if (route.request().method() === 'PATCH') {
      patchBody = JSON.parse(route.request().postData() || '{}');
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ...anchoredNote(20), ...patchBody }),
      });
    } else {
      await route.fallback();
    }
  });

  await openArrangement(page);

  const dot = await page.locator('[data-testid="memo-dot"]').boundingBox();
  if (!dot) throw new Error('memo-dot has no box');
  const cx = dot.x + dot.width / 2;
  const cy = dot.y + dot.height / 2;
  await page.mouse.move(cx, cy);
  await page.mouse.down();
  await page.mouse.move(cx + 30, cy, { steps: 6 });
  await page.mouse.move(cx + 60, cy, { steps: 6 }); // +60px = +10s → 20+10 = 30 (5s grid)
  await page.mouse.up();

  await expect.poll(() => patchBody).not.toBeNull();
  expect(patchBody).toEqual({ anchor_sec: 30 });
});
