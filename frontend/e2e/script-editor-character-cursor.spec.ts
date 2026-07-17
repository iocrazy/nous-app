// Hovering a character cue's NAME must show a pointer cursor — clicking the name
// opens the cast/cue picker (pointHitsText, #1389), but the cursor never reflected
// that, so writers had no hint the name was clickable. The pointer is scoped to
// the name glyphs: the blank space after the name (a caret-placement target) and
// ordinary lines stay on the text cursor.

import { expect, test, type Page } from '@playwright/test';
import {
  SCENE_ID_BASE,
  SCRIPT_URL,
  setupScriptStubs,
  wireScene,
  type WireElement,
} from './helpers/script-stubs';

const els: WireElement[] = [
  { id: 'el_h0001', type: 'action', text: 'The room is quiet.' },
  { id: 'el_h0002', type: 'character', text: 'LIN XIAOMAN' },
  { id: 'el_h0003', type: 'dialogue', text: 'We should go now.' },
];

/** First glyph rect + line box of an element line. */
async function glyphGeom(page: Page, elId: string) {
  return page.evaluate((id) => {
    const line = document.querySelector(`[data-el-id="${id}"]`) as HTMLElement | null;
    if (!line) return null;
    const box = line.getBoundingClientRect();
    const walker = document.createTreeWalker(line, NodeFilter.SHOW_TEXT);
    let n: Node | null;
    while ((n = walker.nextNode())) {
      if (!n.textContent?.trim()) continue;
      const range = document.createRange();
      range.selectNodeContents(n);
      const r = Array.from(range.getClientRects()).filter((x) => x.width > 0 && x.height > 0)[0];
      if (r) return { glyph: { x: r.left, y: r.top, w: r.width, h: r.height }, box: { right: box.right } };
    }
    return { glyph: null, box: { right: box.right } };
  }, elId);
}

async function cursorAt(page: Page, x: number, y: number): Promise<string> {
  await page.mouse.move(x, y); // real mousemove → the NodeView's handler runs
  await page.waitForTimeout(60);
  return page.evaluate(
    ([px, py]) => {
      const el = document.elementFromPoint(px as number, py as number);
      return el ? getComputedStyle(el).cursor : 'none';
    },
    [x, y],
  );
}

for (const format of ['hollywood', 'asian'] as const) {
  test(`character name shows pointer, blank/action stays text (${format})`, async ({ page }) => {
    await setupScriptStubs(page, {
      scenes: [wireScene({ id: SCENE_ID_BASE + 1, sortOrder: 1, location: 'ROOM', elements: els })],
      format,
    });
    await page.goto(SCRIPT_URL);
    await page.waitForSelector('[data-testid="scene-block"]', { timeout: 15_000 });
    await page.waitForTimeout(500);

    const nameG = await glyphGeom(page, 'el_h0002');
    const actionG = await glyphGeom(page, 'el_h0001');
    expect(nameG?.glyph, 'character name must have glyphs').not.toBeNull();

    // Over the name glyphs → pointer.
    const overName = await cursorAt(page, nameG!.glyph!.x + nameG!.glyph!.w / 2, nameG!.glyph!.y + nameG!.glyph!.h / 2);
    expect(overName, 'hovering the character name → pointer').toBe('pointer');

    // Blank space well to the right of the name, still inside the line box → NOT pointer.
    const blankX = Math.min(nameG!.glyph!.x + nameG!.glyph!.w + 80, nameG!.box.right - 6);
    const overBlank = await cursorAt(page, blankX, nameG!.glyph!.y + nameG!.glyph!.h / 2);
    expect(overBlank, 'blank space after the name → not pointer').not.toBe('pointer');

    // Ordinary action line → NOT pointer.
    if (actionG?.glyph) {
      const overAction = await cursorAt(page, actionG.glyph.x + actionG.glyph.w / 2, actionG.glyph.y + actionG.glyph.h / 2);
      expect(overAction, 'action line → not pointer').not.toBe('pointer');
    }
  });
}
