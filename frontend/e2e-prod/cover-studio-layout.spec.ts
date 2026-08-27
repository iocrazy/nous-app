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
        if (el.offsetParent === null) continue; // hidden
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
    await page.setViewportSize(vp);
    await login(page);
    await page.goto(`/team/${WALKTHROUGH_IDS.teamId}/distribution/publish`);

    await page.getByTestId('open-cover-studio').click();
    const modal = page.getByTestId('cover-studio-overlay');
    await expect(modal).toBeVisible();
    // Real data must have arrived: the template grid says "N saved".
    await expect(page.getByTestId('cover-template-grid')).toBeVisible();
    await expect(page.getByTestId('cover-template-folder-note')).toBeVisible();

    await page.screenshot({ path: `test-results/cover-studio-${vp.width}-vertical.png` });
    const vertical = await measureOverflow(page);
    expect(vertical, JSON.stringify(vertical, null, 1)).toEqual([]);

    await page.getByTestId('cover-tab-horizontal').click();
    await expect(page.getByTestId('cover-horizontal-note')).toBeVisible();
    await page.screenshot({ path: `test-results/cover-studio-${vp.width}-horizontal.png` });
    const horizontal = await measureOverflow(page);
    expect(horizontal, JSON.stringify(horizontal, null, 1)).toEqual([]);
  });
}
