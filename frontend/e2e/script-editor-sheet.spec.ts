// The paper must GROW with the script.
//
// `.mh-sheet` sets `min-height:1040px` as an A4-ish floor so an empty script still
// reads as a sheet (editorShellStyles.ts:1564-1566). But the sheet is a column-flex
// item inside `.mh-sheet-scroll` (display:flex; flex-direction:column; overflow-y:auto),
// so the default `flex-shrink:1` let the bounded scroll container squash it back down
// to that floor. The floor became a ceiling: every script taller than 1040px painted
// its overflow outside the paper (the sheet is overflow:visible), which read as
// "the white card ends halfway and the text keeps going".
//
// Content here must exceed 1040px for the assertion to mean anything — the guard
// below fails loudly if a future fixture change makes it too short to test.

import { expect, test } from '@playwright/test';
import { SCRIPT_URL, setupScriptStubs, wireScene, type WireElement } from './helpers/script-stubs';

const PARAGRAPH =
  '林小满快步穿过狭窄的走廊，脚步声在空荡的楼道里回响，她一边整理着手里的文件一边低声念叨着明天要交的报告，' +
  '窗外的雨点砸在玻璃上，整座城市都被浸在一片灰蒙蒙的水汽里，远处的霓虹灯牌闪烁不定，像是随时都会熄灭。';

const els = (n: number): WireElement[] => [
  { id: `el_${n}0000001`, type: 'action', text: PARAGRAPH },
  { id: `el_${n}0000002`, type: 'character', text: 'LIN XIAOMAN' },
  { id: `el_${n}0000003`, type: 'dialogue', text: PARAGRAPH },
];

const SCENES = [1, 2, 3, 4, 5].map((i) =>
  wireScene({
    id: 7300000000000000100 + i,
    sortOrder: i,
    location: `LOCATION ${i}`,
    elements: els(i),
  }),
);

for (const format of ['hollywood', 'asian'] as const) {
  test(`sheet grows past its min-height floor instead of overflowing — ${format}`, async ({
    page,
  }) => {
    await setupScriptStubs(page, { scenes: SCENES, format });
    await page.goto(SCRIPT_URL);
    await page.waitForSelector('[data-testid="scene-block"]', { timeout: 15_000 });
    await page.waitForTimeout(500); // TipTap NodeViews settle

    const m = await page.evaluate(() => {
      const sheet = document.querySelector('.mh-sheet') as HTMLElement | null;
      if (!sheet) throw new Error('no .mh-sheet in DOM');
      const sr = sheet.getBoundingClientRect();
      const inner = sheet.querySelector('.mh-sheet-inner') as HTMLElement | null;
      return {
        sheetHeight: Math.round(sr.height),
        contentHeight: sheet.scrollHeight,
        vOverflow: sheet.scrollHeight - sheet.clientHeight,
        innerOutBottom: inner ? Math.round(inner.getBoundingClientRect().bottom - sr.bottom) : 0,
      };
    });

    // Guard: without enough content the assertions below would pass vacuously.
    expect(m.contentHeight, 'fixture must exceed the 1040px floor to be meaningful').toBeGreaterThan(
      1100,
    );

    expect(m.vOverflow, 'script content must not spill out the bottom of the paper').toBeLessThanOrEqual(1);
    expect(m.innerOutBottom, 'sheet inner must stay inside the paper').toBeLessThanOrEqual(1);
    expect(m.sheetHeight, 'paper must grow to fit its content').toBeGreaterThan(1040);
  });
}
