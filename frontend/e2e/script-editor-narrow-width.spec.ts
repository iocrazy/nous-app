import { test, expect } from '@playwright/test';
import { setupScriptStubs, SCRIPT_URL, SCENE_ID_BASE, wireScene } from './helpers/script-stubs';

/**
 * Regression: at a narrow viewport the paper must never be CLIPPED by
 * `.mh-editor-shell{ overflow:hidden }`, and never be SQUASHED below the
 * width the fixed Hollywood ch-indents need.
 *
 * Root cause (image-90 report): the shell grid's middle (script) track was a
 * bare `1fr`, whose implicit minimum is the min-content of the fixed-width
 * `.mh-sheet` (780px). At narrow widths the track floored at ~780px, pushed
 * the column past the shell's right edge, and overflow:hidden clipped the
 * paper — the writer saw the paragraph's right side cut off.
 *
 * Fix has two halves, and both need guarding:
 *  - `minmax(0, 1fr)` lets the track shrink so `.mh-sheet{max-width:100%}`
 *    fits the paper to the window (no more clipping);
 *  - `.mh-sheet{min-width:480px}` floors the shrink — dialogue/character carry
 *    fixed ch padding (~200px) that never shrinks, so an unbounded squash
 *    would render dialogue one character per line. Below the floor the
 *    sheet-scroll shows a horizontal scrollbar instead.
 */

const LONG_CJK =
  '阿萨德发生地方阿萨德啊撒且分啊撒且分啊撒且啊按双方阿萨德发生地方阿萨德啊撒且分啊撒且分啊分按双方阿萨德发生地方阿萨德啊撒且分啊撒且分啊分按双方';

function fixtureScenes() {
  return [
    wireScene({
      id: SCENE_ID_BASE + 1,
      sortOrder: 1,
      location: 'Alley',
      elements: [
        { id: 'el_0001', type: 'action', text: LONG_CJK },
        { id: 'el_0002', type: 'character', text: 'NARRATOR' },
        { id: 'el_0003', type: 'dialogue', text: 'A dialogue line long enough to wrap once or twice.' },
      ],
    }),
  ];
}

async function measure(page: import('@playwright/test').Page) {
  return page.evaluate(() => {
    const shell = document.querySelector('.mh-editor-shell') as HTMLElement;
    const scroll = document.querySelector('.mh-sheet-scroll') as HTMLElement;
    const sheet = document.querySelector('.mh-sheet') as HTMLElement;
    const dialogue = document.querySelector('.mh-el-editable[data-el-type="dialogue"]') as HTMLElement;
    const shellR = shell.getBoundingClientRect();
    const scrollR = scroll.getBoundingClientRect();
    const sheetR = sheet.getBoundingClientRect();
    const dlgR = dialogue.getBoundingClientRect();
    const dlgStyle = getComputedStyle(dialogue);
    const dlgTextWidth =
      dlgR.width - parseFloat(dlgStyle.paddingLeft) - parseFloat(dlgStyle.paddingRight);
    return {
      shellRight: Math.round(shellR.right),
      scrollLeft: scroll.scrollLeft,
      scrollHasHScroll: scroll.scrollWidth > scroll.clientWidth + 1,
      scrollClientLeft: Math.round(scrollR.left),
      sheetLeft: Math.round(sheetR.left),
      sheetRight: Math.round(sheetR.right),
      sheetWidth: Math.round(sheetR.width),
      /* Content width available to dialogue TEXT after the fixed ch padding. */
      dialogueTextWidth: Math.round(dlgTextWidth),
    };
  });
}

test('panel collapsed (image-90 state): paper fits, dialogue stays readable', async ({ page }) => {
  await page.setViewportSize({ width: 960, height: 840 });
  await setupScriptStubs(page, { format: 'hollywood', scenes: fixtureScenes() });
  await page.goto(SCRIPT_URL);
  await page.waitForSelector('.mh-el-editable', { timeout: 20_000 });

  // Collapse the right Writing panel — the reported layout.
  await page.locator('.mh-panel-head .mh-icon-btn').click();
  await page.waitForTimeout(400);

  const m = await measure(page);
  // The paper fits: right edge inside the shell, no horizontal scrollbar needed.
  expect(m.sheetRight).toBeLessThanOrEqual(m.shellRight);
  expect(m.scrollHasHScroll).toBe(false);
  // It actually shrank below the fixed 780px width to fit…
  expect(m.sheetWidth).toBeLessThan(780);
  // …but not below the floor, so dialogue text keeps a readable measure.
  expect(m.sheetWidth).toBeGreaterThanOrEqual(480);
  expect(m.dialogueTextWidth).toBeGreaterThanOrEqual(100);
});

test('both panels expanded at 960px: floor holds, scrollbar instead of squash', async ({ page }) => {
  await page.setViewportSize({ width: 960, height: 840 });
  await setupScriptStubs(page, { format: 'hollywood', scenes: fixtureScenes() });
  await page.goto(SCRIPT_URL);
  await page.waitForSelector('.mh-el-editable', { timeout: 20_000 });

  const m = await measure(page);
  // The paper never squashes below the ch-indent floor…
  expect(m.sheetWidth).toBeGreaterThanOrEqual(480);
  // …dialogue keeps a usable text column (not one character per line)…
  expect(m.dialogueTextWidth).toBeGreaterThanOrEqual(100);
  // …and the overflow is handled by a scrollbar on the sheet scroller,
  // with the paper's LEFT edge reachable at scrollLeft=0 (margin-inline:auto,
  // not align-items:center, so a wide paper is not centered into unreachable
  // negative-x space — the classic centered-overflow data-loss trap).
  expect(m.scrollHasHScroll).toBe(true);
  expect(m.scrollLeft).toBe(0);
  expect(m.sheetLeft).toBeGreaterThanOrEqual(m.scrollClientLeft - 1);
});
