// Reordering an element row inside a scene must emit a move op — and nothing else.
//
// This is the path the [DRAG-DBG] tracing was added to diagnose (a sync setState
// in onDragStart re-rendered the row mid-drag and the browser aborted the drag;
// ProseMirror's built-in DnD then hijacked the drop). It's covered here against a
// real browser drag so the console tracing can go.

import { expect, test } from '@playwright/test';
import {
  SCENE_ID_BASE,
  SCRIPT_URL,
  dragGrip,
  setupScriptStubs,
  wireScene,
  type WireElement,
} from './helpers/script-stubs';

const ELEMENTS: WireElement[] = [
  { id: 'el_00000001', type: 'action', text: 'FIRST action line.' },
  { id: 'el_00000002', type: 'action', text: 'SECOND action line.' },
  { id: 'el_00000003', type: 'action', text: 'THIRD action line.' },
];

const SCENES = [
  wireScene({ id: SCENE_ID_BASE + 1, sortOrder: 1, location: 'CORRIDOR', elements: ELEMENTS }),
];

test('dragging an element row reorders it within the scene', async ({ page }) => {
  const ops: unknown[] = [];
  await setupScriptStubs(page, { scenes: SCENES, format: 'hollywood' });
  await page.route('**/api/v1/scenes/*/elements/ops', async (route) => {
    ops.push(JSON.parse(route.request().postData() || '{}'));
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: { elements: [], content_version: 2 } }),
    });
  });

  await page.goto(SCRIPT_URL);
  await page.waitForSelector('[data-testid="scene-block"]', { timeout: 15_000 });
  await page.waitForTimeout(500);

  const rows = () =>
    page.$$eval('[data-el-type="action"]', (els) => els.map((e) => (e.textContent || '').trim()));

  expect(await rows(), 'fixture must render three ordered rows').toEqual([
    'FIRST action line.',
    'SECOND action line.',
    'THIRD action line.',
  ]);

  // Drag the THIRD row's grip onto the FIRST row.
  const third = page.locator('[data-el-type="action"]').nth(2);
  await third.hover();
  await page.waitForTimeout(200);
  await dragGrip(page, page.locator('.mh-el-drag').nth(2), page.locator('[data-el-type="action"]').nth(0));
  await page.waitForTimeout(800);

  // A move op — never an insert/update. An insert here is the signature of the
  // drag being hijacked into a text paste rather than a reorder.
  const flat = ops.flatMap((o) => (o as { ops: { op: string }[] }).ops ?? []);
  expect(flat.length, 'the drag must dispatch exactly one op').toBe(1);
  expect(flat[0]).toMatchObject({ op: 'move', element_id: 'el_00000003' });

  expect(await rows(), 'the third row must now lead').toEqual([
    'THIRD action line.',
    'FIRST action line.',
    'SECOND action line.',
  ]);
});
