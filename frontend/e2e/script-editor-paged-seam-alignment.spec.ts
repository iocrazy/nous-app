// Paged-mode page seams must bleed to BOTH paper edges, in BOTH rendering
// contexts: the scene-level seam (EditorShell's <PageSeam>, a direct child of
// .mh-sheet-inner) and the element-level seam (pageSeamPlugin widget, rendered
// inside the TipTap editor, which sits inside .mh-scene-block's 4px left
// padding). The two used to share one hardcoded negative margin, so the
// in-editor seam landed ~4px short of the left paper edge while the scene-level
// one bled correctly — the "paged L/R spacing looks off" report. This asserts
// both contexts hit the edges (±1px) and align with each other.

import { expect, test } from '@playwright/test';
import {
  SCENE_ID_BASE,
  SCRIPT_ID,
  SCRIPT_URL,
  setupScriptStubs,
  wireScene,
  type WireElement,
} from './helpers/script-stubs';

test('paged seams bleed to both paper edges in both contexts', async ({ page }) => {
  const tall: WireElement[] = [];
  for (let i = 1; i <= 70; i += 1) {
    tall.push({ id: `el_s${String(i).padStart(4, '0')}`, type: 'action', text: `Seam row ${i} content.` });
  }
  const scenes = [wireScene({ id: SCENE_ID_BASE + 1, sortOrder: 1, location: 'TALL', elements: tall })];
  for (let s = 2; s <= 30; s += 1) {
    scenes.push(wireScene({ id: SCENE_ID_BASE + s, sortOrder: s, location: `SC ${s}`, elements: [
      { id: `el_${s}a`, type: 'action', text: `Scene ${s} line.` },
    ] }));
  }

  await page.addInitScript((id) => {
    try {
      localStorage.setItem(`editor.pagination.${id}`, 'paged');
    } catch {
      /* ignore */
    }
  }, SCRIPT_ID);
  await setupScriptStubs(page, { scenes, format: 'hollywood' });
  await page.goto(SCRIPT_URL);
  await page.waitForSelector('[data-testid="scene-block"]', { timeout: 15_000 });
  await page.waitForTimeout(1200); // let the ResizeObserver settle the seam layout

  const data = await page.evaluate(() => {
    const sheet = document.querySelector('.mh-sheet') as HTMLElement | null;
    if (!sheet) return null;
    const sr = sheet.getBoundingClientRect();
    return {
      sheetLeft: sr.left,
      sheetRight: sr.right,
      seams: Array.from(document.querySelectorAll<HTMLElement>('.mh-page-seam')).map((el) => {
        const r = el.getBoundingClientRect();
        const numEl = el.querySelector<HTMLElement>('.mh-page-seam-num');
        const numR = numEl?.getBoundingClientRect();
        const filler = parseFloat(getComputedStyle(el).paddingTop) || 0;
        return {
          context: el.closest('.mh-tiptap-scene-editor') ? 'element' : 'scene',
          leftOverhang: sr.left - r.left, // 0 = flush with paper left edge
          rightOverhang: r.right - sr.right, // 0 = flush with paper right edge
          left: r.left,
          numRightFromSheet: numR ? sr.right - numR.right : null,
          numText: numEl?.textContent ?? null,
          chromeHeight: r.height - filler, // non-filler seam height
        };
      }),
    };
  });

  expect(data, 'sheet must render').not.toBeNull();
  const seams = data!.seams;
  const elementSeams = seams.filter((s) => s.context === 'element');
  const sceneSeams = seams.filter((s) => s.context === 'scene');
  // Both contexts must actually appear so the assertions below have teeth.
  expect(elementSeams.length, 'an in-editor (element) seam must render').toBeGreaterThan(0);
  expect(sceneSeams.length, 'a scene-level seam must render').toBeGreaterThan(0);

  // Every seam bleeds to both paper edges within 1px.
  for (const s of seams) {
    expect(Math.abs(s.leftOverhang), `${s.context} seam flush to left edge`).toBeLessThanOrEqual(1);
    expect(Math.abs(s.rightOverhang), `${s.context} seam flush to right edge`).toBeLessThanOrEqual(1);
  }
  // The two contexts align with each other on the left.
  const elLeft = elementSeams[0].left;
  const scLeft = sceneSeams[0].left;
  expect(Math.abs(elLeft - scLeft), 'element vs scene seam left aligned').toBeLessThanOrEqual(1);
  // Page number sits the same distance from the right paper edge in both.
  const elNum = elementSeams[0].numRightFromSheet ?? -1;
  const scNum = sceneSeams[0].numRightFromSheet ?? -1;
  expect(Math.abs(elNum - scNum), 'page-number right offset consistent').toBeLessThanOrEqual(1);

  // Laper style: the page number is the ENDED page (a bare integer, no
  // trailing period). Scene 1 has 70 rows, so its first seam ends page 1.
  for (const s of seams) {
    expect(s.numText, `${s.context} seam page number is a bare integer`).toMatch(/^\d+$/);
  }
  expect(elementSeams[0].numText, 'element seam shows the ended page number').toBe('1');

  // The seam's non-filler chrome height matches SEAM_CHROME_PX (the single
  // dashed rule row), within 1px, in both contexts.
  for (const s of seams) {
    expect(Math.abs(s.chromeHeight - 40), `${s.context} seam chrome height = SEAM_CHROME_PX`).toBeLessThanOrEqual(1);
  }
});
