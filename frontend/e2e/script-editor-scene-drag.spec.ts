// Dragging a whole scene must move the scene — and write nothing into the script.
//
// Every reorder drag (scene handle, scene move overlay, element grip) puts its id
// in `text/plain` so it can leave the app at all. The drop lands on a
// contentEditable, whose native default is to paste the dragged text, so a scene
// reorder used to fire its move AND append the scene's own id to whatever line it
// landed on — a correct move plus a corrupted script. Skipping ProseMirror's drop
// never helped: returning true from `handleDOMEvents` doesn't preventDefault, and
// PM was not the thing writing the text. See MH_DRAG_MIME in editor/types.ts.
//
// The ops-endpoint assertion is the regression: an ops call during a pure scene
// drag means something wrote to the doc.

import { expect, test } from '@playwright/test';
import {
  SCENE_ID_BASE,
  SCRIPT_ID,
  SCRIPT_URL,
  setupScriptStubs,
  wireScene,
  type WireElement,
} from './helpers/script-stubs';

const els = (n: number): WireElement[] => [
  { id: `el_${n}0000001`, type: 'action', text: `Action line in scene ${n}.` },
  { id: `el_${n}0000002`, type: 'character', text: 'LIN XIAOMAN' },
  { id: `el_${n}0000003`, type: 'dialogue', text: `Dialogue in scene ${n}.` },
];

const sceneOf = (i: number) =>
  wireScene({ id: SCENE_ID_BASE + i, sortOrder: i, location: `LOCATION ${i}`, elements: els(i) });

test('dragging a scene reorders it and writes nothing into the script', async ({ page }) => {
  // A backend that actually persists the move: the shell fires moveScene then
  // reload()s, so a fixed list would hide whether the move landed at all.
  let order = [1, 2, 3];
  const opsCalls: string[] = [];
  let moveBody: unknown = null;

  await setupScriptStubs(page, { scenes: [], format: 'hollywood' });

  await page.route(`**/api/v1/scripts/${SCRIPT_ID}/scenes`, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: order.map((n, idx) => ({ ...sceneOf(n), sort_order: idx + 1 })),
      }),
    }),
  );

  await page.route('**/api/v1/scenes/*/move', async (route) => {
    const body = JSON.parse(route.request().postData() || '{}');
    moveBody = body;
    const movedId = Number(route.request().url().split('/scenes/')[1].split('/move')[0]);
    const anchorId = Number(body.before_scene_id ?? body.after_scene_id);
    const moved = movedId - SCENE_ID_BASE;
    const anchor = anchorId - SCENE_ID_BASE;
    const rest = order.filter((n) => n !== moved);
    const at = rest.indexOf(anchor);
    rest.splice(body.before_scene_id ? at : at + 1, 0, moved);
    order = rest;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: sceneOf(moved) }),
    });
  });

  await page.route('**/api/v1/scenes/*/elements/ops', async (route) => {
    opsCalls.push(route.request().postData() || '');
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: { elements: [], content_version: 2 } }),
    });
  });

  await page.goto(SCRIPT_URL);
  await page.waitForSelector('[data-testid="scene-block"]', { timeout: 15_000 });
  await page.waitForTimeout(500);

  const ids = () =>
    page.$$eval('[data-testid="scene-block"]', (bs) => bs.map((b) => b.getAttribute('data-scene-id')));

  const before = await ids();
  // Guard: distinct ids, or every id-keyed assertion here is meaningless. Scene
  // ids are 53-bit-safe by design (migrations/050); a fixture that outgrows
  // Number.MAX_SAFE_INTEGER silently collapses them all to one value.
  expect(new Set(before).size, 'fixture scene ids must survive JSON parsing distinct').toBe(3);

  const third = page.locator('[data-testid="scene-block"]').nth(2);
  await third.hover();
  await third.locator('.mh-drag-handle').dragTo(page.locator('[data-testid="scene-block"]').nth(0));
  await page.waitForTimeout(800);

  expect(moveBody, 'the drag must ask the backend to move the scene').toEqual({
    before_scene_id: String(SCENE_ID_BASE + 1),
  });

  // The regression: a pure scene drag must not touch the document.
  expect(opsCalls, 'a scene drag must not write elements').toEqual([]);

  expect(await ids(), 'scene 3 must land ahead of scene 1').toEqual([
    String(SCENE_ID_BASE + 3),
    String(SCENE_ID_BASE + 1),
    String(SCENE_ID_BASE + 2),
  ]);

  // And the line it was dropped onto must be untouched prose.
  const firstAction = await page.locator('[data-el-type="action"]').first().textContent();
  expect(firstAction, 'the drop target line must not have the dragged id pasted into it').toBe(
    'Action line in scene 3.',
  );
});
