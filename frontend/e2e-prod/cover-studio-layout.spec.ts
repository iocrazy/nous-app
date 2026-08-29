// Real-stack layout check for Cover Studio — the v4 three-column dialog.
//
// Not in CI. Run after a frontend deploy:
//   cd frontend && npx playwright test -c e2e-prod/playwright.config.ts cover-studio-layout
//
// What it proves: at the two viewports the design was drawn for, the dialog
// opens over the publish page, and NO column overflows its box — measured,
// not eyeballed. Two rounds of the design mock were caught overflowing by the
// user; the estimate is what was wrong both times, so this asserts on numbers.
import { expect, test } from '@playwright/test';
import { loadProdCreds, WALKTHROUGH_IDS } from './helpers';

const creds = loadProdCreds();

async function login(page: import('@playwright/test').Page) {
  await page.goto('/login');
  const trigger = page.getByText('Log in', { exact: true });
  await expect(trigger).toBeVisible();
  await trigger.click();
  await page.locator('input[type="email"]').fill(creds.email);
  const pw = page.locator('input[type="password"]');
  await pw.fill(creds.password);
  await pw.locator('..').locator('xpath=following-sibling::button[1]').click();
  await page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 20_000 });
}

interface Overflow {
  selector: string;
  column: string;
  by: number;
}

/** Every element that sticks out of its column by more than 1px. */
async function measureOverflow(page: import('@playwright/test').Page): Promise<Overflow[]> {
  return page.evaluate(() => {
    const out: { selector: string; column: string; by: number }[] = [];
    const cols = ['.cs-col-left', '.cs-col-stage', '.cs-col-side'];
    for (const col of cols) {
      const box = document.querySelector(col);
      if (!box) continue;
      const cr = box.getBoundingClientRect();
      for (const el of Array.from(box.querySelectorAll<HTMLElement>('*'))) {
        if (el.offsetParent === null) continue; // display:none subtree
        // Tooltip bubbles (.cs-tip) sit absolutely positioned and invisible
        // until hover; they have a box but nobody can see it. Only what is
        // painted counts as overflow.
        const cs = getComputedStyle(el);
        if (cs.visibility === 'hidden' || cs.opacity === '0') continue;
        const r = el.getBoundingClientRect();
        if (r.width === 0) continue;
        const by = Math.max(r.right - cr.right, cr.left - r.left);
        if (by > 1) {
          const id = el.getAttribute('data-testid');
          out.push({
            selector: id ? `[data-testid=${id}]` : `${el.tagName.toLowerCase()}.${el.className}`.slice(0, 80),
            column: col,
            by: Math.round(by),
          });
        }
      }
    }
    const cols_ = document.querySelector<HTMLElement>('.cs-cols');
    if (cols_ && cols_.scrollWidth > cols_.clientWidth + 1) {
      out.push({ selector: '.cs-cols(horizontal scroll)', column: 'grid', by: cols_.scrollWidth - cols_.clientWidth });
    }
    return out;
  });
}

for (const vp of [
  { width: 1440, height: 900 },
  { width: 1280, height: 800 },
]) {
  test(`cover studio fits its three columns at ${vp.width}×${vp.height}`, async ({ page }) => {
    // Cold load + login + library picker + the studio's own loads on the real
    // stack: the default 45s is too tight for one honest pass.
    test.setTimeout(120_000);
    await page.setViewportSize(vp);
    await login(page);
    await page.goto(`/team/${WALKTHROUGH_IDS.teamId}/distribution/publish`);

    // A cold load of the publish page on the real stack can take well over
    // the 15s click default (seen 2026-08-27; the retry passed in 5s).
    const addTile = page.locator('.thumb.add').first();
    await expect(addTile).toBeVisible({ timeout: 45_000 });

    // Since #2037 the cover slots are blocked until a video is selected —
    // pick the first library video the way a user would.
    await addTile.click();
    const firstVideo = page.locator('.picker-item').first();
    await expect(firstVideo).toBeVisible({ timeout: 30_000 });
    await firstVideo.click();
    await page.locator('.btn.btn-solid', { hasText: /Done|完成/ }).first().click();

    const open = page.getByTestId('open-cover-studio');
    await expect(open).toBeVisible();
    await expect(open).not.toHaveClass(/blocked/);
    await open.click();
    const modal = page.getByTestId('cover-studio-overlay');
    await expect(modal).toBeVisible();
    // The library is a picker now: the tile opens it against real data.
    await page.getByTestId('cover-ref-add-template').click();
    await expect(page.getByTestId('cover-picker-grid')).toBeVisible();
    await expect(page.getByTestId('cover-picker-count')).toBeVisible();
    // UiModal has no Escape handler; close the way a user would — Cancel.
    await page.locator('.cover-studio-modal').getByRole('button', { name: /Cancel|取消/ }).first().click();
    await expect(page.getByTestId('cover-picker-grid')).toBeHidden();

    await page.screenshot({ path: `test-results/cover-studio-${vp.width}-vertical.png` });
    const vertical = await measureOverflow(page);
    expect(vertical, JSON.stringify(vertical, null, 1)).toEqual([]);

    await page.getByTestId('cover-tab-horizontal').click();
    // Since #2023 the horizontal cover goes through the model too: the
    // generate button stays, and the preview switches to 4:3.
    await expect(page.getByTestId('cover-generate')).toBeVisible();
    await expect(page.getByTestId('cover-preview')).toHaveClass(/\bh\b/);
    await page.screenshot({ path: `test-results/cover-studio-${vp.width}-horizontal.png` });
    const horizontal = await measureOverflow(page);
    expect(horizontal, JSON.stringify(horizontal, null, 1)).toEqual([]);
  });
}
