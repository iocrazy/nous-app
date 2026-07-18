import { test, expect } from '@playwright/test';
import {
  setupScriptStubs,
  SCRIPT_URL,
  SCRIPT_ID,
  SCENE_ID_BASE,
  wireScene,
  zoomKey,
  type WireElement,
} from './helpers/script-stubs';

/**
 * Display-zoom feature (Writing panel → Zoom). Zoom is PURELY VISUAL: a CSS
 * `zoom` on the sheet reflows the paper while the stored document, the export
 * semantics and — critically — the pagination break points stay identical.
 *
 * Coverage:
 *  1. the +/- control scales the sheet's visual size proportionally;
 *  2. break INVARIANCE — the seam beforeKey sequence is byte-identical at 100%
 *     and 150% (the measurement divides its readings back to logical px);
 *  3. per-script persistence survives a reload;
 *  4. the product default is 115% when nothing is stored.
 */

// A long single scene whose action rows overflow several pages, so the seam
// sequence is non-trivial and sensitive to any measurement drift.
function longSceneFixture() {
  const elements: WireElement[] = [];
  for (let i = 0; i < 90; i += 1) {
    elements.push({ id: `el_${i}`, type: 'action', text: `Action line number ${i} on the page.` });
  }
  return [wireScene({ id: SCENE_ID_BASE + 1, sortOrder: 1, location: 'Studio', elements })];
}

async function seedPaged(page: import('@playwright/test').Page) {
  await page.addInitScript(
    ([key]) => {
      try {
        localStorage.setItem(key as string, 'paged');
      } catch (err) {
        console.error('init pagination seed failed', err);
      }
    },
    [`editor.pagination.${SCRIPT_ID}`] as const,
  );
}

/** Ordered list of the row key each seam sits BEFORE — the DOM-observable
 *  equivalent of computePageLayout's seam.beforeKey sequence. */
function readSeamBeforeKeys(page: import('@playwright/test').Page) {
  return page.evaluate(() => {
    const sheet = document.querySelector('.mh-sheet');
    if (!sheet) return [] as string[];
    const nodes = sheet.querySelectorAll<HTMLElement>(
      '.mh-page-seam, .mh-scene-headrow, .mh-scene-placeholder, [data-el-id]',
    );
    const seq: string[] = [];
    let pendingSeam = false;
    for (const n of Array.from(nodes)) {
      if (n.classList.contains('mh-page-seam')) {
        pendingSeam = true;
        continue;
      }
      let key: string | null = null;
      if (n.classList.contains('mh-scene-headrow')) {
        const sid = (n.closest('[data-scene-id]') as HTMLElement | null)?.dataset.sceneId;
        key = sid ? `scene:${sid}` : null;
      } else if (n.classList.contains('mh-scene-placeholder')) {
        key = n.dataset.sceneId ? `scene:${n.dataset.sceneId}` : null;
      } else if (n.dataset.elId) {
        key = n.dataset.elId;
      }
      if (pendingSeam && key) {
        seq.push(key);
        pendingSeam = false;
      }
    }
    return seq;
  });
}

test('zoom control scales the sheet proportionally', async ({ page }) => {
  // Wide viewport so the zoomed sheet is never clamped by the pane width.
  await page.setViewportSize({ width: 2200, height: 1400 });
  await setupScriptStubs(page, { scenes: longSceneFixture(), format: 'hollywood', zoom: 100 });
  await page.goto(SCRIPT_URL);
  await expect(page.getByTestId('scene-block')).toHaveCount(1);

  const value = page.getByTestId('zoom-value');
  await expect(value).toHaveText('100%');

  const width100 = (await page.locator('.mh-sheet').boundingBox())!.width;

  // 100 → 115 → 125 → 150 (three tiers up).
  await page.getByTestId('zoom-in').click();
  await page.getByTestId('zoom-in').click();
  await page.getByTestId('zoom-in').click();
  await expect(value).toHaveText('150%');

  const width150 = (await page.locator('.mh-sheet').boundingBox())!.width;
  expect(width150 / width100).toBeGreaterThan(1.45);
  expect(width150 / width100).toBeLessThan(1.55);

  // Persisted as a BARE number string (matches zoomStorage's writer).
  const stored = await page.evaluate((k) => localStorage.getItem(k), zoomKey(SCRIPT_ID));
  expect(stored).toBe('150');

  // Clicking the percentage resets to the 115% default.
  await value.click();
  await expect(value).toHaveText('115%');
});

test('zoom persists per script across a reload', async ({ page }) => {
  // Seed nothing (zoom:null) so the harness does not re-seed on reload — the
  // point of this test is that the WRITTEN value survives the reload.
  await setupScriptStubs(page, { scenes: longSceneFixture(), format: 'hollywood', zoom: null });
  await page.goto(SCRIPT_URL);

  const value = page.getByTestId('zoom-value');
  await expect(value).toHaveText('115%'); // app default

  // 115 → 125 → 150 (two tiers up), then reload and confirm it stuck.
  await page.getByTestId('zoom-in').click();
  await page.getByTestId('zoom-in').click();
  await expect(value).toHaveText('150%');
  expect(await page.evaluate((k) => localStorage.getItem(k), zoomKey(SCRIPT_ID))).toBe('150');

  await page.reload();
  await expect(page.getByTestId('zoom-value')).toHaveText('150%');
});

test('pagination break points are identical at 100% and 150% zoom', async ({ page }) => {
  await page.setViewportSize({ width: 2200, height: 1400 });
  await seedPaged(page);

  // 100% baseline.
  await setupScriptStubs(page, { scenes: longSceneFixture(), format: 'hollywood', zoom: 100 });
  await page.goto(SCRIPT_URL);
  await expect(page.locator('.mh-page-seam').first()).toBeVisible();
  // Let the ResizeObserver settle before snapshotting.
  await page.waitForTimeout(200);
  const keys100 = await readSeamBeforeKeys(page);
  expect(keys100.length).toBeGreaterThan(1);

  // Same document at 150% — drive the control up three tiers, then re-snapshot.
  await page.getByTestId('zoom-in').click();
  await page.getByTestId('zoom-in').click();
  await page.getByTestId('zoom-in').click();
  await expect(page.getByTestId('zoom-value')).toHaveText('150%');
  await page.waitForTimeout(200);
  const keys150 = await readSeamBeforeKeys(page);

  expect(keys150).toEqual(keys100);
});

test('paged seams still bleed to both paper edges at 150% zoom', async ({ page }) => {
  await page.setViewportSize({ width: 2200, height: 1400 });
  await seedPaged(page);
  // One tall scene (so an element-level seam appears) + short scenes after it
  // (so a scene-level seam appears too) — the same two contexts the alignment
  // spec guards, now under zoom. Negative margins scale with `zoom`, so the
  // flush-to-edge geometry must survive.
  const tall: WireElement[] = [];
  for (let i = 1; i <= 70; i += 1) {
    tall.push({ id: `el_s${i}`, type: 'action', text: `Seam row ${i} content.` });
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
  await setupScriptStubs(page, { scenes, format: 'hollywood', zoom: 100 });
  await page.goto(SCRIPT_URL);
  await expect(page.locator('.mh-page-seam').first()).toBeVisible();

  // Drive to 150% and let the seam layout re-settle.
  await page.getByTestId('zoom-in').click();
  await page.getByTestId('zoom-in').click();
  await page.getByTestId('zoom-in').click();
  await expect(page.getByTestId('zoom-value')).toHaveText('150%');
  await page.waitForTimeout(400);

  const data = await page.evaluate(() => {
    const sheet = document.querySelector('.mh-sheet') as HTMLElement | null;
    if (!sheet) return null;
    const sr = sheet.getBoundingClientRect();
    return Array.from(document.querySelectorAll<HTMLElement>('.mh-page-seam')).map((el) => {
      // Measure the dashed rule row directly — its getBoundingClientRect is in
      // the zoomed space, so no getComputedStyle/filler ambiguity.
      const rule = el.querySelector<HTMLElement>('.mh-page-seam-rule');
      const rr = rule!.getBoundingClientRect();
      return {
        context: el.classList.contains('mh-page-seam-inline') ? 'element' : 'scene',
        leftOverhang: sr.left - rr.left,
        rightOverhang: rr.right - sr.right,
        // Visual chrome height = SEAM_CHROME_PX(40) × zoom(1.5) ≈ 60.
        chromeHeight: rr.height,
      };
    });
  });

  expect(data, 'sheet must render').not.toBeNull();
  const seams = data!;
  expect(seams.some((s) => s.context === 'element'), 'an element seam renders').toBe(true);
  expect(seams.some((s) => s.context === 'scene'), 'a scene seam renders').toBe(true);
  // Flush to both edges — tolerance scaled by the 1.5× zoom (sub-pixel × 1.5).
  for (const s of seams) {
    expect(Math.abs(s.leftOverhang), `${s.context} seam flush left @150%`).toBeLessThanOrEqual(2);
    expect(Math.abs(s.rightOverhang), `${s.context} seam flush right @150%`).toBeLessThanOrEqual(2);
    // Chrome ≈ 40 × 1.5 = 60px visual.
    expect(Math.abs(s.chromeHeight - 60), `${s.context} seam chrome scaled @150%`).toBeLessThanOrEqual(3);
  }
});

test('display zoom defaults to 115% when nothing is stored', async ({ page }) => {
  await setupScriptStubs(page, { scenes: longSceneFixture(), format: 'hollywood', zoom: null });
  await page.goto(SCRIPT_URL);
  await expect(page.getByTestId('zoom-value')).toHaveText('115%');
  const zoomAttr = await page.locator('.mh-sheet').getAttribute('data-zoom');
  expect(zoomAttr).toBe('115');
});
