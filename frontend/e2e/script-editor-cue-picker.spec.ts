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

import { expect, test, type Locator, type Page } from '@playwright/test';
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

// ── Create-a-character parity with the location dropdown ────────────────────
//
// The footer promises "Select or create". Before the fix the picker only ever
// SELECTED: a substring candidate ("55555555") auto-highlighted, so Enter picked
// it and the writer's freshly typed name ("5555555") could never become its own
// cue. And with no candidate at all, the line-driven Enter just closed the popup.
// Contract (location-dropdown parity): a non-empty query that matches nothing
// EXACTLY appends a navigable "Create '<name>'" row; Enter on it (or on the sole
// create row when nothing matches) writes the new name — which, since the cast is
// the set of distinct cues, is exactly how a character is born.

const cueRow = (page: Page) => page.locator('[data-el-type="character"]').nth(1);
const createRow = (page: Page) => page.locator('.mh-mention-opt.create');

/** Open the picker on the empty cue line (index 1) and return its search input. */
async function openEmptyCue(page: Page): Promise<Locator> {
  const box = await cueRow(page).boundingBox();
  expect(box, 'fixture must render an empty character row').not.toBeNull();
  await page.mouse.click(box!.x + box!.width / 2, box!.y + box!.height / 2);
  await expect(picker(page)).toBeVisible();
  return picker(page).locator('input');
}

test('a brand-new name offers a Create row and Enter creates the character', async ({ page }) => {
  await open(page);
  const input = await openEmptyCue(page);
  await input.fill('NEW HERO');
  // Nothing in the cast matches, so the sole navigable row is the Create row.
  await expect(createRow(page)).toBeVisible();
  await expect(createRow(page)).toContainText('NEW HERO');
  await input.press('Enter');
  await expect(picker(page)).toBeHidden();
  await expect(cueRow(page)).toHaveText(/NEW HERO/);
  // The new cue joins the cast (distinct-cue derivation) — the rail lists it.
  await expect(page.locator('.mh-rail-entity-name', { hasText: 'NEW HERO' })).toBeVisible();
});

test('a partial match keeps Create reachable — Enter on it coins the shorter name', async ({
  page,
}) => {
  await open(page);
  const input = await openEmptyCue(page);
  // "LIN" is a substring of the existing "LIN XIAOMAN": the candidate highlights
  // first, but the Create row must still be there to reach.
  await input.fill('LIN');
  await expect(createRow(page)).toContainText('LIN');
  await input.press('ArrowDown'); // step off the highlighted candidate onto Create
  await input.press('Enter');
  await expect(picker(page)).toBeHidden();
  await expect(cueRow(page)).toHaveText(/^LIN$/); // the new short name, not "LIN XIAOMAN"
});

test('an exact match selects and never creates', async ({ page }) => {
  await open(page);
  const input = await openEmptyCue(page);
  await input.fill('LIN XIAOMAN');
  // Exact hit → no synthetic Create row; Enter commits the existing cue.
  await expect(createRow(page)).toHaveCount(0);
  await input.press('Enter');
  await expect(picker(page)).toBeHidden();
  await expect(cueRow(page)).toHaveText(/LIN XIAOMAN/);
});

test('the line keyboard path also creates on Enter when nothing matches', async ({ page }) => {
  await open(page);
  // No search-box focus this time: type straight into the cue line.
  const box = await cueRow(page).boundingBox();
  await page.mouse.click(box!.x + box!.width / 2, box!.y + box!.height / 2);
  await expect(picker(page)).toBeVisible();
  await page.keyboard.type('NOVA');
  await expect(createRow(page)).toContainText('NOVA');
  await page.keyboard.press('Enter');
  await expect(picker(page)).toBeHidden();
  await expect(cueRow(page)).toHaveText(/NOVA/);
});

test('the create affordance works in Asian format too', async ({ page }) => {
  await open(page, 'asian');
  const input = await openEmptyCue(page);
  await input.fill('5555555');
  await expect(createRow(page)).toContainText('5555555');
  await input.press('Enter');
  await expect(picker(page)).toBeHidden();
  await expect(cueRow(page)).toHaveText(/5555555/);
});
