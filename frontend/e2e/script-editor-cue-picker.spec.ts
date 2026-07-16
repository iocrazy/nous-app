// The character cue picker must be INVITED, not sprung.
//
// It used to open on any selection landing in a character line — so placing the
// caret to edit the name, arrowing through the script, or focusing the line
// programmatically all popped the dropdown over the writer's text. Contract
// (laper parity, confirmed with the user 2026-07-15):
//
//   opens   — a real click on the cue NAME itself; any caret in an EMPTY
//             character line (an empty cue has nothing to offer but the cast)
//   silent  — click on the blank space after the name (that's placing a caret
//             to edit), keyboard navigation into the line, typing in a line
//             whose picker isn't already open
//
// Typing while the picker IS open still filters it — that path is the query
// bridge, not an open trigger.

import { expect, test, type Page } from '@playwright/test';
import { SCRIPT_URL, setupScriptStubs, wireScene, type WireElement, SCENE_ID_BASE } from './helpers/script-stubs';

const ELEMENTS: WireElement[] = [
  { id: 'el_00000001', type: 'action', text: 'Rain hammers the window.' },
  { id: 'el_00000002', type: 'character', text: 'LIN XIAOMAN' },
  { id: 'el_00000003', type: 'dialogue', text: 'We are out of time.' },
  { id: 'el_00000004', type: 'character', text: '' },
];

const SCENES = [
  wireScene({ id: SCENE_ID_BASE + 1, sortOrder: 1, location: 'CORRIDOR', elements: ELEMENTS }),
];

const picker = (page: Page) => page.locator('[data-testid="mention-combobox"]');

/**
 * Tight glyph rects for a row's text.
 *
 * Must range over the TEXT NODES, not the element: selectNodeContents on a block
 * yields line boxes, which span the block's full width and would report the blank
 * space to the right of a short cue as "text".
 */
const RECTS_FN = `(row) => {
  const walker = document.createTreeWalker(row, NodeFilter.SHOW_TEXT);
  const rects = [];
  let n;
  while ((n = walker.nextNode())) {
    if (!n.textContent || !n.textContent.trim()) continue;
    const r = document.createRange();
    r.selectNodeContents(n);
    for (const rect of Array.from(r.getClientRects())) {
      if (rect.width > 0 && rect.height > 0) rects.push(rect);
    }
  }
  return rects;
}`;

/** Viewport point at the centre of a character row's rendered glyphs. */
async function textPoint(page: Page, index: number) {
  return page.evaluate(
    ([i, fnSrc]) => {
      const row = Array.from(document.querySelectorAll('[data-el-type="character"]'))[
        i as number
      ] as HTMLElement;
      const rects = (eval(fnSrc as string) as (el: HTMLElement) => DOMRect[])(row);
      const r = rects[0];
      return r ? { x: r.left + r.width / 2, y: r.top + r.height / 2 } : null;
    },
    [index, RECTS_FN] as const,
  );
}

/** Viewport point in a character row's trailing blank space, past the glyphs. */
async function blankPoint(page: Page, index: number) {
  return page.evaluate(
    ([i, fnSrc]) => {
      const row = Array.from(document.querySelectorAll('[data-el-type="character"]'))[
        i as number
      ] as HTMLElement;
      const box = row.getBoundingClientRect();
      const rects = (eval(fnSrc as string) as (el: HTMLElement) => DOMRect[])(row);
      const textRight = rects.length ? Math.max(...rects.map((r) => r.right)) : box.left;
      const x = textRight + (box.right - textRight) / 2;
      return { x, y: box.top + box.height / 2, textRight, boxRight: box.right };
    },
    [index, RECTS_FN] as const,
  );
}

async function open(page: Page, format: 'hollywood' | 'asian' = 'hollywood') {
  await setupScriptStubs(page, { scenes: SCENES, format });
  await page.goto(SCRIPT_URL);
  await page.waitForSelector('[data-testid="scene-block"]', { timeout: 15_000 });
  await page.waitForTimeout(500);
}

test('clicking the cue name opens the picker', async ({ page }) => {
  await open(page);
  const p = await textPoint(page, 0);
  expect(p, 'fixture must render character text to click').not.toBeNull();
  await page.mouse.click(p!.x, p!.y);
  await expect(picker(page)).toBeVisible();
});

test('clicking the blank space after the cue name only places the caret', async ({ page }) => {
  await open(page);
  const p = await blankPoint(page, 0);
  // Guard: if there's no blank space to the right, this test proves nothing.
  expect(p.boxRight - p.textRight, 'row must have trailing blank space to click').toBeGreaterThan(20);
  await page.mouse.click(p.x, p.y);
  await page.waitForTimeout(300);
  await expect(picker(page)).toBeHidden();
});

test('an empty character line offers the cast', async ({ page }) => {
  await open(page);
  const box = await page
    .locator('[data-el-type="character"]')
    .nth(1)
    .boundingBox();
  expect(box, 'fixture must render an empty character row').not.toBeNull();
  await page.mouse.click(box!.x + box!.width / 2, box!.y + box!.height / 2);
  await expect(picker(page)).toBeVisible();
});

test('arrowing into a character line does not pop the picker', async ({ page }) => {
  await open(page);
  // Start in the action line above the cue, then walk the caret down into it.
  const action = await page.locator('[data-el-type="action"]').first().boundingBox();
  await page.mouse.click(action!.x + 20, action!.y + action!.height / 2);
  await page.waitForTimeout(200);
  await expect(picker(page)).toBeHidden();

  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('End');
  await page.waitForTimeout(300);
  await expect(picker(page)).toBeHidden();
});
