// A multi-line parenthetical must wear its closing ')' at the END of the LAST
// line, wrapping naturally with the text — not stranded at the top-right of the
// first line. @tiptap/react's NodeViewContent wraps the row text in a BLOCK
// <div>; left block, the '(' / ')' pseudos land on their OWN lines above/below
// the text (and the retired display:flex stranded ')' on the first line). The
// fix makes that inner wrapper inline so the pseudos flow inline with the text.
// This spec locks that in geometrically: the paren block adds NO extra lines
// beyond the text's own (so '(' and ')' ride the first/last text lines).

import { expect, test } from '@playwright/test';
import { SCENE_ID_BASE, SCRIPT_URL, setupScriptStubs, wireScene, type WireElement } from './helpers/script-stubs';

const LONG_PAREN =
  'whispering under her breath so the others in the crowded room would not catch a single word of it';

test('multi-line parenthetical: closing paren rides the last line, flows inline', async ({ page }) => {
  const elements: WireElement[] = [
    { id: 'el_p0000001', type: 'character', text: 'LIN' },
    { id: 'el_p0000002', type: 'paren', text: LONG_PAREN },
    { id: 'el_p0000003', type: 'dialogue', text: 'A short line.' },
  ];
  const scenes = [wireScene({ id: SCENE_ID_BASE + 1, sortOrder: 1, location: 'ROOM', elements })];

  await setupScriptStubs(page, { scenes, format: 'hollywood' });
  await page.goto(SCRIPT_URL);
  await page.waitForSelector('[data-testid="scene-block"]', { timeout: 15_000 });
  await page.waitForTimeout(500);

  const data = await page.evaluate(() => {
    const paren = document.querySelector<HTMLElement>('.hw-paren[data-el-type="paren"]');
    if (!paren) return null;
    const wrapper = paren.querySelector<HTMLElement>('[data-node-view-content-react]');
    // Text line boxes (pseudos '(' / ')' are not part of the range).
    const range = document.createRange();
    range.selectNodeContents(paren);
    const rects = Array.from(range.getClientRects()).filter((r) => r.width > 0 && r.height > 0);
    const lineCount = new Set(rects.map((r) => Math.round(r.top))).size;
    const lineHeight = parseFloat(getComputedStyle(paren).lineHeight) || 23;
    return {
      display: getComputedStyle(paren).display,
      wrapperDisplay: wrapper ? getComputedStyle(wrapper).display : null,
      lineCount,
      lineHeight,
      parenHeight: paren.getBoundingClientRect().height,
      // Extra height beyond the text lines: 0 lines when '(' / ')' ride the
      // first/last text lines; ~2 lines when they each take their own line.
      extraLines: Math.round(
        (paren.getBoundingClientRect().height - lineCount * lineHeight) / lineHeight,
      ),
      afterContent: getComputedStyle(paren, '::after').content,
      parenText: paren.textContent,
    };
  });

  expect(data, 'paren must render').not.toBeNull();
  // Fixture must actually wrap, or the closing-paren-position bug can't show.
  expect(data!.lineCount, 'parenthetical must wrap to multiple lines').toBeGreaterThanOrEqual(2);
  // NOT the retired one-line flex.
  expect(data!.display, 'parenthetical is not a single flex line').not.toBe('flex');
  // The inner content wrapper is inline, so the parens flow with the text.
  expect(data!.wrapperDisplay, 'inner content wrapper is inline').toBe('inline');
  // The paren block adds no line of its own for '(' or ')': they ride the
  // first/last TEXT lines. (Block wrapper bug → extraLines ≈ 2.)
  expect(data!.extraLines, "'(' and ')' add no extra lines — they ride the text").toBeLessThanOrEqual(0);
  // Parens are pseudo decoration; the stored text carries neither.
  expect(data!.parenText ?? '', 'stored text has no literal parens').not.toContain('(');
  expect(data!.afterContent, 'closing paren is rendered as ::after decoration').toContain(')');
});
