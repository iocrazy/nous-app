// Asian (华语) format parity with laper's 亚洲格式 (this feature branch). Covers the
// four structural rules and the per-type indent hierarchy:
//   • scene head reorders to `N. 地点 时间 / INT|EXT` (number leads, INT/EXT trails)
//   • action carries a △ prefix, character a ：suffix, wrapped dialogue a ∟
//   • per-type indents (heading/character 0, action/dialogue 4ch, paren 0.5ch, …)
//   • asian paper uses narrower symmetric 8ch/8ch margins (vs hollywood 15/10)
//   • those margins keep the paged page-seam bleeding to both paper edges
//
// The e2e harness seeds `format` via JSON.stringify (harness bug #1428), which
// readStoredFormat() never matches — so setupScriptStubs' format:'asian' silently
// renders hollywood. Every test here re-seeds the RAW value AFTER (mirroring
// script-editor-comment-quote.spec.ts) and asserts `.as-row` actually rendered
// before measuring, so a regression back to hollywood fails loudly instead of
// quietly measuring the wrong engine.

import { expect, test, type Page } from '@playwright/test';
import {
  SCENE_ID_BASE,
  SCRIPT_ID,
  SCRIPT_URL,
  formatKey,
  setupScriptStubs,
  wireScene,
  type WireElement,
} from './helpers/script-stubs';

// Long enough to wrap past the 60ch text column (both engines) so the multi-line
// paths (action hanging indent, dialogue ∟) actually engage.
const LONG =
  'This dialogue line is deliberately long enough that it must wrap across more ' +
  'than a single rendered line inside the sixty-character measure of the sheet.';

const ELEMENTS: WireElement[] = [
  { id: 'el_a0000001', type: 'action', text: LONG },
  { id: 'el_a0000002', type: 'action', text: '' }, // empty action → still gets △ + placeholder
  { id: 'el_c0000001', type: 'character', text: 'ALEX' },
  { id: 'el_d0000001', type: 'dialogue', text: LONG }, // multi-line → ∟
  { id: 'el_d0000002', type: 'dialogue', text: 'OK.' }, // single-line → no ∟
  { id: 'el_p0000001', type: 'paren', text: 'smiling' },
  { id: 'el_m0000001', type: 'comment', text: 'A note to the director about the tone here.' },
  { id: 'el_t0000001', type: 'transition', text: 'CUT TO' },
  { id: 'el_s0000001', type: 'subtitle', text: 'Somewhere in the city' },
];

async function gotoAsian(page: Page, elements: WireElement[] = ELEMENTS): Promise<void> {
  const scenes = [wireScene({ id: SCENE_ID_BASE + 1, sortOrder: 1, location: 'STAGE', elements })];
  await setupScriptStubs(page, { scenes, format: 'asian' });
  // Re-seed the RAW value so readStoredFormat() actually returns 'asian'.
  await page.addInitScript(
    ([key, value]) => {
      try {
        localStorage.setItem(key as string, value as string);
      } catch {
        /* ignore */
      }
    },
    [formatKey(SCRIPT_ID), 'asian'] as const,
  );
  await page.goto(SCRIPT_URL);
  await page.waitForSelector('[data-testid="scene-block"]', { timeout: 15_000 });
  await page.waitForTimeout(600); // let the ResizeObserver settle the ∟ class
  // Engine gate: the asian NodeView wraps every row in `.as-row`. If this is
  // missing the run silently fell back to hollywood and nothing below is valid.
  expect(await page.locator('.as-row').count(), 'asian engine rendered (.as-row present)').toBeGreaterThan(0);
}

test('asian scene head renders N. Location Time / INT|EXT (number leads, INT/EXT trails)', async ({
  page,
}) => {
  await gotoAsian(page);

  const head = await page.evaluate(() => {
    const heading = document.querySelector<HTMLElement>('.mh-scene-heading.asian');
    if (!heading) return null;
    const num = heading.querySelector<HTMLElement>('.mh-scene-num-inline');
    const selects = Array.from(heading.querySelectorAll<HTMLElement>('.mh-scene-select'));
    return {
      numText: num?.textContent ?? null,
      numWeight: num ? parseInt(getComputedStyle(num).fontWeight, 10) : 0,
      numLeft: num?.getBoundingClientRect().left ?? null,
      selects: selects.map((s) => ({ text: s.textContent?.trim() ?? '', left: s.getBoundingClientRect().left })),
    };
  });

  expect(head, 'asian heading must render').not.toBeNull();
  // The scene number leads as a bold inline prefix (not the hover-reveal margin badge).
  expect(head!.numText, 'scene number is an inline "N." prefix').toMatch(/^\d+\.$/);
  expect(head!.numWeight, 'scene number is bold').toBeGreaterThanOrEqual(700);

  const texts = head!.selects.map((s) => s.text);
  // wireScene seeds location=STAGE, time=DAY, int_ext=INT. Asian order = location, time, INT/EXT.
  expect(texts, 'three head tokens in order location → time → INT/EXT').toEqual(['STAGE', 'DAY', 'INT']);

  // Visual left order confirms the reflow: number, then location, time, then INT/EXT.
  const [loc, time, intExt] = head!.selects;
  expect(head!.numLeft!, 'number sits left of location').toBeLessThan(loc.left);
  expect(loc.left, 'location left of time').toBeLessThan(time.left);
  expect(time.left, 'time left of INT/EXT (INT/EXT trails)').toBeLessThan(intExt.left);
});

test('asian scene numbers are sequential scene ordinals (1. 2. 3.), not continuous block numbers', async ({
  page,
}) => {
  // Three scenes with DIFFERENT element counts. The old inline number reused the
  // continuous document-order block index (blockIndexBase + 1), so the scene
  // headers jumped (1 → 5 → 7). The asian scene number must be the scene ordinal
  // (its position among scenes), matching the left rail's S1/S2/S3.
  const scenes = [
    wireScene({
      id: SCENE_ID_BASE + 1,
      sortOrder: 1,
      location: 'STAGE',
      elements: [
        { id: 'el_1a', type: 'action', text: 'One.' },
        { id: 'el_1b', type: 'action', text: 'Two.' },
        { id: 'el_1c', type: 'action', text: 'Three.' },
      ],
    }),
    wireScene({
      id: SCENE_ID_BASE + 2,
      sortOrder: 2,
      location: 'ALLEY',
      elements: [{ id: 'el_2a', type: 'action', text: 'Solo.' }],
    }),
    wireScene({
      id: SCENE_ID_BASE + 3,
      sortOrder: 3,
      location: 'ROOF',
      elements: [
        { id: 'el_3a', type: 'action', text: 'A.' },
        { id: 'el_3b', type: 'action', text: 'B.' },
        { id: 'el_3c', type: 'action', text: 'C.' },
        { id: 'el_3d', type: 'action', text: 'D.' },
        { id: 'el_3e', type: 'action', text: 'E.' },
      ],
    }),
  ];
  await setupScriptStubs(page, { scenes, format: 'asian' });
  await page.addInitScript(
    ([key, value]) => {
      try {
        localStorage.setItem(key as string, value as string);
      } catch {
        /* ignore */
      }
    },
    [formatKey(SCRIPT_ID), 'asian'] as const,
  );
  await page.goto(SCRIPT_URL);
  await page.waitForSelector('[data-testid="scene-block"]', { timeout: 15_000 });
  expect(await page.locator('.as-row').count(), 'asian engine rendered').toBeGreaterThan(0);

  const nums = await page.evaluate(() =>
    Array.from(document.querySelectorAll<HTMLElement>('.mh-scene-num-inline')).map(
      (el) => el.textContent?.trim() ?? '',
    ),
  );
  expect(nums, 'inline scene numbers are sequential scene ordinals').toEqual(['1.', '2.', '3.']);
});

test('asian per-type indent hierarchy (relative to sheet content box)', async ({ page }) => {
  await gotoAsian(page);

  const geo = await page.evaluate(() => {
    const sheet = document.querySelector<HTMLElement>('.mh-sheet');
    if (!sheet) return null;
    const padL = parseFloat(getComputedStyle(sheet).paddingLeft) || 0;
    const contentLeft = sheet.getBoundingClientRect().left + padL;

    // 1ch in a given element's own font (letter-spacing is ~0 for the body types
    // measured here, so a plain 10-glyph probe is accurate).
    const chFor = (el: HTMLElement): number => {
      const cs = getComputedStyle(el);
      const probe = document.createElement('span');
      probe.style.cssText = `position:absolute;visibility:hidden;white-space:pre;font-family:${cs.fontFamily};font-size:${cs.fontSize};font-weight:${cs.fontWeight};font-style:${cs.fontStyle}`;
      probe.textContent = '0000000000';
      document.body.appendChild(probe);
      const w = probe.getBoundingClientRect().width / 10;
      probe.remove();
      return w;
    };

    // TEXT position = the editable's CONTENT box left (past any border + padding),
    // so a bordered/padded row (comment) reports where its glyphs sit, not its
    // border-box edge.
    const textLeftOf = (type: string): number | null => {
      const el = document.querySelector<HTMLElement>(`.mh-el-editable[data-el-type="${type}"]`);
      if (!el) return null;
      const cs = getComputedStyle(el);
      const inset = (parseFloat(cs.borderLeftWidth) || 0) + (parseFloat(cs.paddingLeft) || 0);
      return el.getBoundingClientRect().left + inset - contentLeft;
    };
    // BORDER-box left = where the comment's quote bar (its left border) sits.
    const borderLeftOf = (type: string): number | null => {
      const el = document.querySelector<HTMLElement>(`.mh-el-editable[data-el-type="${type}"]`);
      return el ? el.getBoundingClientRect().left - contentLeft : null;
    };

    // The per-type indents live on the `.as-row-*` wrappers, which inherit the
    // sheet's Courier grid (--script-mono) — NOT the row's CJK body font — so
    // every asian indent sits on the SAME ch grid as the sheet margins. Measure
    // ch in that Courier context (the sheet's own font).
    return {
      sheetCh: sheet ? chFor(sheet) : 0,
      character: textLeftOf('character'),
      action: textLeftOf('action'),
      dialogue: textLeftOf('dialogue'),
      paren: textLeftOf('paren'),
      comment: textLeftOf('comment'),
      commentBar: borderLeftOf('comment'),
    };
  });

  expect(geo, 'sheet + rows must render').not.toBeNull();
  const ch = geo!.sheetCh;
  expect(ch, 'measured a real ch width').toBeGreaterThan(3);

  // The flush-left (0ch) character row IS the indent baseline. It sits a few px
  // in from the sheet's padding edge — a constant structural offset (the scene
  // block's 4px left padding + editor container) that every row shares, so the
  // per-type indents below are measured RELATIVE to it to cancel that offset.
  const base = geo!.character!;
  expect(base, 'character flush left, only the small structural offset from the content box').toBeLessThanOrEqual(8);

  // action + dialogue share the 4ch body column.
  expect(Math.abs(geo!.action! - base - 4 * ch), 'action body at 4ch').toBeLessThanOrEqual(2);
  expect(Math.abs(geo!.dialogue! - geo!.action!), 'dialogue aligned with action body').toBeLessThanOrEqual(2);
  // paren tucks in at 0.5ch.
  expect(Math.abs(geo!.paren! - base - 0.5 * ch), 'paren at 0.5ch').toBeLessThanOrEqual(2);
  // comment quote bar sits at ~0.5ch; its text lands near the 4ch column (the
  // reach past the bar is on the CJK-font editable, whose ch is a touch narrower
  // than the sheet's Courier ch, so the text tolerance is slightly looser).
  expect(Math.abs(geo!.commentBar! - base - 0.5 * ch), 'comment quote bar at 0.5ch').toBeLessThanOrEqual(2);
  expect(Math.abs(geo!.comment! - base - 4 * ch), 'comment text near 4ch column').toBeLessThanOrEqual(6);
});

test('asian sheet uses narrower symmetric 8ch/8ch margins', async ({ page }) => {
  await gotoAsian(page);

  const m = await page.evaluate(() => {
    const sheet = document.querySelector<HTMLElement>('.mh-sheet.asian');
    if (!sheet) return null;
    const cs = getComputedStyle(sheet);
    const padL = parseFloat(cs.paddingLeft) || 0;
    const padR = parseFloat(cs.paddingRight) || 0;
    // Sheet ch (Courier --script-mono) from a probe in the sheet's own font.
    const probe = document.createElement('span');
    probe.style.cssText = `position:absolute;visibility:hidden;white-space:pre;font-family:${cs.fontFamily};font-size:${cs.fontSize}`;
    probe.textContent = '0000000000';
    sheet.appendChild(probe);
    const ch = probe.getBoundingClientRect().width / 10;
    probe.remove();
    return { padL, padR, ch };
  });

  expect(m, 'asian sheet must render').not.toBeNull();
  // Symmetric.
  expect(Math.abs(m!.padL - m!.padR), 'left/right margins symmetric').toBeLessThanOrEqual(1);
  // 8ch each.
  expect(Math.abs(m!.padL - 8 * m!.ch), 'left margin ≈ 8ch').toBeLessThanOrEqual(2);
  expect(Math.abs(m!.padR - 8 * m!.ch), 'right margin ≈ 8ch').toBeLessThanOrEqual(2);
});

test('asian ornaments △ / ： / ∟ render, are non-editable, and never enter the doc', async ({
  page,
}) => {
  await gotoAsian(page);

  const marks = await page.evaluate(() => {
    const prefix = document.querySelector<HTMLElement>('.as-row-action > .as-prefix');
    const suffix = document.querySelector<HTMLElement>('.as-row-character .as-suffix');
    // The long dialogue wraps → its row gets .as-multiline (the ∟ pseudo);
    // the short "OK." dialogue does not.
    const dialogueRows = Array.from(document.querySelectorAll<HTMLElement>('.as-row-dialogue'));
    const multi = dialogueRows.find((r) => r.classList.contains('as-multiline')) ?? null;
    const single = dialogueRows.find((r) => !r.classList.contains('as-multiline')) ?? null;
    const contBefore = multi ? getComputedStyle(multi, '::before').content : null;
    const singleBefore = single ? getComputedStyle(single, '::before').content : null;
    return {
      prefixText: prefix?.textContent ?? null,
      prefixEditable: prefix?.isContentEditable ?? null,
      suffixText: suffix?.textContent ?? null,
      suffixEditable: suffix?.isContentEditable ?? null,
      multiExists: !!multi,
      contBefore,
      singleBefore,
    };
  });

  // △ action prefix — present, non-editable.
  expect(marks.prefixText, 'action row shows △').toBe('△');
  expect(marks.prefixEditable, '△ is not editable').toBe(false);
  // ： character suffix — present, non-editable.
  expect(marks.suffixText, 'character row shows the fullwidth ：').toBe('：');
  expect(marks.suffixEditable, '： is not editable').toBe(false);
  // ∟ continuation — a pseudo-element on the WRAPPED dialogue row only.
  expect(marks.multiExists, 'a wrapped dialogue row is flagged .as-multiline').toBe(true);
  expect(marks.contBefore, 'wrapped dialogue renders the ∟ continuation glyph').toContain('∟');
  // Single-line dialogue has NO ∟ (laper parity).
  expect(marks.singleBefore === 'none' || marks.singleBefore === '' || marks.singleBefore == null, 'single-line dialogue has no ∟').toBe(true);
});

test('asian ornament glyphs are not part of the editable text (doc integrity)', async ({ page }) => {
  await gotoAsian(page);
  // The △ / ： marks live OUTSIDE .mh-el-editable, so the editable text of an
  // action / character row must not contain them.
  const leak = await page.evaluate(() => {
    const action = document.querySelector<HTMLElement>('.as-row-action .mh-el-editable');
    const character = document.querySelector<HTMLElement>('.as-row-character .mh-el-editable');
    return {
      actionHasTriangle: (action?.textContent ?? '').includes('△'),
      characterHasColon: (character?.textContent ?? '').includes('：'),
    };
  });
  expect(leak.actionHasTriangle, '△ is decoration, not editable text').toBe(false);
  expect(leak.characterHasColon, '： is decoration, not editable text').toBe(false);
});

test('asian + paged: page seams still bleed to both paper edges (8ch margins)', async ({ page }) => {
  // A tall scene so pagination inserts at least one seam.
  const tall: WireElement[] = [];
  for (let i = 1; i <= 70; i += 1) {
    tall.push({ id: `el_s${String(i).padStart(4, '0')}`, type: 'action', text: `Seam row ${i} content.` });
  }
  const scenes = [wireScene({ id: SCENE_ID_BASE + 1, sortOrder: 1, location: 'TALL', elements: tall })];
  for (let s = 2; s <= 20; s += 1) {
    scenes.push(
      wireScene({
        id: SCENE_ID_BASE + s,
        sortOrder: s,
        location: `SC ${s}`,
        elements: [{ id: `el_${s}a`, type: 'action', text: `Scene ${s} line.` }],
      }),
    );
  }

  await page.addInitScript((id) => {
    try {
      localStorage.setItem(`editor.pagination.${id}`, 'paged');
    } catch {
      /* ignore */
    }
  }, SCRIPT_ID);
  await setupScriptStubs(page, { scenes, format: 'asian' });
  await page.addInitScript(
    ([key, value]) => {
      try {
        localStorage.setItem(key as string, value as string);
      } catch {
        /* ignore */
      }
    },
    [formatKey(SCRIPT_ID), 'asian'] as const,
  );
  await page.goto(SCRIPT_URL);
  await page.waitForSelector('[data-testid="scene-block"]', { timeout: 15_000 });
  await page.waitForTimeout(1200); // let the ResizeObserver settle the seam layout

  const data = await page.evaluate(() => {
    const sheet = document.querySelector<HTMLElement>('.mh-sheet.asian');
    if (!sheet) return null;
    const sr = sheet.getBoundingClientRect();
    return {
      seams: Array.from(document.querySelectorAll<HTMLElement>('.mh-page-seam')).map((el) => {
        const r = el.getBoundingClientRect();
        return {
          context: el.closest('.mh-tiptap-scene-editor') ? 'element' : 'scene',
          leftOverhang: sr.left - r.left,
          rightOverhang: r.right - sr.right,
        };
      }),
    };
  });

  expect(data, 'asian paged sheet must render').not.toBeNull();
  expect(data!.seams.length, 'at least one page seam rendered').toBeGreaterThan(0);
  for (const s of data!.seams) {
    expect(Math.abs(s.leftOverhang), `${s.context} seam flush to left edge`).toBeLessThanOrEqual(1);
    expect(Math.abs(s.rightOverhang), `${s.context} seam flush to right edge`).toBeLessThanOrEqual(1);
  }
});
