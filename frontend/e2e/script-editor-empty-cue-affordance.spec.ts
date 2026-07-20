// An EMPTY character cue must ADVERTISE itself (laper parity, user 2026-07-20):
//
//   1. The grey "Character" whisper stays VISIBLE while the line is focused and
//      sits UNDER the centered caret — not orphaned at the page's left edge.
//      (The hollywood cue is text-align:center; the absolute placeholder overlay
//      was pinned left:0, so the hint rendered far from the caret and read as
//      "no hint at all".)
//   2. Retyping a line INTO an empty character cue opens the cast picker even
//      though the caret never moves (Tab / toolbar / slash all keep the caret in
//      place, so onSelectionUpdate can't do it — the keymap flags the
//      transaction, the imperative retypeElement invites directly).
//   3. Clicking an empty cue that ALREADY holds the caret re-opens the picker
//      (no selection change → no selectionUpdate → the DOM click handler is the
//      only path).
//
// Asian format keeps its left-aligned cue, so the base left:0 overlay is
// already correct there — asserted to stay that way.

import { expect, test, type Page } from '@playwright/test';
import { SCRIPT_URL, setupScriptStubs, wireScene, type WireElement, SCENE_ID_BASE } from './helpers/script-stubs';

const ELEMENTS: WireElement[] = [
  { id: 'el_ec0001', type: 'action', text: 'Rain hammers the window.' },
  { id: 'el_ec0002', type: 'character', text: 'LIN XIAOMAN' },
  { id: 'el_ec0003', type: 'dialogue', text: 'We are out of time.' },
  { id: 'el_ec0004', type: 'action', text: '' },
];

const SCENES = [
  wireScene({ id: SCENE_ID_BASE + 1, sortOrder: 1, location: 'CORRIDOR', elements: ELEMENTS }),
];

const picker = (page: Page) => page.locator('[data-testid="mention-combobox"]');
const emptyRow = (page: Page) => page.locator('[data-el-id="el_ec0004"]');

async function open(page: Page, format: 'hollywood' | 'asian' = 'hollywood') {
  await setupScriptStubs(page, { scenes: SCENES, format });
  await page.goto(SCRIPT_URL);
  await page.waitForSelector('[data-testid="scene-block"]', { timeout: 15_000 });
  await page.waitForTimeout(500);
}

/** Click into the empty trailing line and Tab it into a character cue. */
async function tabIntoEmptyCue(page: Page) {
  const box = await emptyRow(page).boundingBox();
  expect(box, 'fixture must render the empty trailing action row').not.toBeNull();
  await page.mouse.click(box!.x + box!.width / 2, box!.y + box!.height / 2);
  await page.waitForTimeout(200);
  await page.keyboard.press('Tab'); // action → character (TAB_TYPE)
  await expect(emptyRow(page)).toHaveAttribute('data-el-type', 'character');
}

test('Tab-retype into an empty cue opens the cast picker without moving the caret', async ({
  page,
}) => {
  await open(page);
  await tabIntoEmptyCue(page);
  await expect(picker(page)).toBeVisible();
  // laper parity: blank query, full cast on offer.
  await expect(picker(page).locator('input')).toHaveValue('');
  await expect(picker(page).locator('.mh-mention-opt', { hasText: 'LIN XIAOMAN' })).toBeVisible();
});

test('re-clicking an empty cue that already holds the caret re-opens the picker', async ({
  page,
}) => {
  await open(page);
  await tabIntoEmptyCue(page);
  await expect(picker(page)).toBeVisible();

  // Dismiss, then click the SAME line again — no selection change happens, so
  // only the DOM click fallback can re-invite it.
  await page.keyboard.press('Escape');
  await expect(picker(page)).toBeHidden();
  const box = await emptyRow(page).boundingBox();
  await page.mouse.click(box!.x + box!.width / 2, box!.y + box!.height / 2);
  await expect(picker(page)).toBeVisible();
});

test('picking a character lands the caret in a fresh dialogue line with the coaching hint', async ({
  page,
}) => {
  await open(page);
  await tabIntoEmptyCue(page);
  await expect(picker(page)).toBeVisible();

  // Pick LIN XIAOMAN from the cast list.
  await picker(page).locator('.mh-mention-opt', { hasText: 'LIN XIAOMAN' }).click();
  await expect(picker(page)).toBeHidden();
  await expect(emptyRow(page)).toHaveText('LIN XIAOMAN');

  // laper: a dialogue line materialises right below the cue and holds the
  // caret, whispering its coaching hint.
  const dialogue = await page.evaluate(() => {
    // Document order: the element right after the cue.
    const all = Array.from(document.querySelectorAll('[data-el-id]'));
    const idx = all.findIndex((el) => el.getAttribute('data-el-id') === 'el_ec0004');
    const next = all[idx + 1] as HTMLElement | undefined;
    if (!next) return null;
    const sel = document.getSelection();
    return {
      type: next.dataset.elType,
      hint: getComputedStyle(next, '::before').content,
      caretInside: !!sel?.anchorNode && next.contains(sel.anchorNode),
    };
  });
  expect(dialogue).not.toBeNull();
  expect(dialogue!.type).toBe('dialogue');
  expect(dialogue!.caretInside).toBe(true);
  expect(dialogue!.hint).toContain('Enter dialogue');
});

test('picking a character reuses an existing following dialogue instead of inserting', async ({
  page,
}) => {
  // Cue directly above an existing dialogue: selecting must NOT wedge a new
  // empty dialogue between them.
  const paired: WireElement[] = [
    { id: 'el_pd0001', type: 'character', text: 'LIN XIAOMAN' },
    { id: 'el_pd0002', type: 'dialogue', text: 'We are out of time.' },
    { id: 'el_pd0003', type: 'action', text: '' },
  ];
  await setupScriptStubs(page, {
    scenes: [wireScene({ id: SCENE_ID_BASE + 1, sortOrder: 1, location: 'LAB', elements: paired })],
  });
  await page.goto(SCRIPT_URL);
  await page.waitForSelector('[data-testid="scene-block"]', { timeout: 15_000 });
  await page.waitForTimeout(500);

  // Open the picker on the EXISTING cue by clicking its name.
  const cue = page.locator('[data-el-id="el_pd0001"]');
  await cue.click();
  await expect(picker(page)).toBeVisible();
  await picker(page).locator('.mh-mention-opt', { hasText: 'LIN XIAOMAN' }).first().click();
  await expect(picker(page)).toBeHidden();

  // Still exactly one dialogue, directly after the cue — caret inside it.
  const state = await page.evaluate(() => {
    const all = Array.from(document.querySelectorAll('[data-el-id]'));
    const types = all.map((el) => (el as HTMLElement).dataset.elType);
    const sel = document.getSelection();
    const dlg = document.querySelector('[data-el-id="el_pd0002"]');
    return {
      types,
      caretInExisting: !!sel?.anchorNode && !!dlg && dlg.contains(sel.anchorNode),
    };
  });
  expect(state.types.filter((t) => t === 'dialogue')).toHaveLength(1);
  expect(state.caretInExisting).toBe(true);
});

test('hollywood: the focused empty cue shows a CENTERED "Character" whisper', async ({ page }) => {
  await open(page);
  await tabIntoEmptyCue(page);

  const overlay = await emptyRow(page).evaluate((el) => {
    const s = getComputedStyle(el, '::before');
    return { content: s.content, textAlign: s.textAlign, left: s.left, right: s.right };
  });
  expect(overlay.content).toBe('"Character"');
  // left:0 + right:0 stretches the overlay across the row; text-align centres
  // the whisper under the centered caret.
  expect(overlay.textAlign).toBe('center');
  expect(overlay.left).toBe('0px');
  expect(overlay.right).toBe('0px');

  // The empty cue is a button in disguise (click → cast picker): it must
  // advertise with a pointer cursor, not the text caret (laper parity).
  const cursor = await emptyRow(page).evaluate((el) => getComputedStyle(el).cursor);
  expect(cursor).toBe('pointer');
});

test('asian: the focused empty cue keeps its LEFT-aligned whisper', async ({ page }) => {
  await open(page, 'asian');
  await tabIntoEmptyCue(page);

  const overlay = await emptyRow(page).evaluate((el) => {
    const s = getComputedStyle(el, '::before');
    return { content: s.content, textAlign: s.textAlign, whiteSpace: s.whiteSpace };
  });
  expect(overlay.content).toBe('"Character"');
  expect(overlay.textAlign).not.toBe('center');
  // The asian cue shrink-wraps: an empty cue is ~0 wide, and without nowrap the
  // whisper rendered one letter per line down the margin.
  expect(overlay.whiteSpace).toBe('nowrap');
});
