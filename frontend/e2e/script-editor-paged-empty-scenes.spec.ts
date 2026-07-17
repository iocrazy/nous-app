import { test, expect } from '@playwright/test';
import { setupScriptStubs, SCRIPT_URL, SCRIPT_ID, SCENE_ID_BASE, wireScene } from './helpers/script-stubs';

/**
 * Regression: paged mode + a run of EMPTY scenes must not blank out page 1
 * (prod 图92 — 12 mostly-empty scenes rendered as one scene + a full page of
 * filler, with every other scene pushed past the "2." seam).
 *
 * Root cause: an empty scene renders EmptySceneHint (no `.mh-el-row`), so the
 * measurement sees consecutive 'heading' rows. computePageLayout's
 * keep-with-next cascade treated every heading as binding to the next row, so
 * a run of headings chained the cascade back to the page start:
 * filler = pageHeight - (rows[1].top - rows[0].top) ≈ a whole page.
 *
 * Fix (paginate.ts bindsToNext): a heading/cue only keeps with its OWN scene's
 * content — when the next row starts a NEW scene (heading/placeholder), the
 * keep rule does not apply and the break stays at the overflow row.
 */

const EMPTY_SCENES = 20; // stays under WINDOW_THRESHOLD (30) — no windowing

function fixtureScenes() {
  return Array.from({ length: EMPTY_SCENES }, (_, i) =>
    wireScene({
      id: SCENE_ID_BASE + 1 + i,
      sortOrder: i + 1,
      location: `Empty Location ${i + 1}`,
      elements: [],
    }),
  );
}

test('paged mode: a run of empty scenes never leaves page 1 as one scene + full-page filler', async ({
  page,
}) => {
  await setupScriptStubs(page, { scenes: fixtureScenes(), format: 'hollywood' });
  // Seed paged mode the way the shell persists it (raw string, not JSON).
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

  await page.goto(SCRIPT_URL);
  await expect(page.getByTestId('scene-block')).toHaveCount(EMPTY_SCENES);
  // Wait for the seam layout to converge (ResizeObserver → computePageLayout).
  await expect(page.locator('.mh-page-seam').first()).toBeVisible();

  const geom = await page.evaluate(() => {
    const seam = document.querySelector('.mh-page-seam') as HTMLElement;
    const seamTop = seam.getBoundingClientRect().top;
    const filler = parseFloat(getComputedStyle(seam).paddingTop);
    const headrowsAboveSeam = Array.from(
      document.querySelectorAll<HTMLElement>('.mh-scene-headrow'),
    ).filter((el) => el.getBoundingClientRect().top < seamTop).length;
    return { filler, headrowsAboveSeam };
  });

  // Buggy cascade: exactly 1 scene above the seam and filler ≈ the whole page
  // (~1100px of blank paper). Fixed: page 1 is packed with scenes and the
  // filler is just the leftover below the last row that fits.
  expect(geom.headrowsAboveSeam).toBeGreaterThan(1);
  expect(geom.filler).toBeLessThan(630); // < half of PAGE_CONTENT_PX (1260)
});
