import { test, expect, type Page } from '@playwright/test';
import { loadProdCreds } from './helpers';

const RID = process.env.VP_RESOURCE_ID!;

async function signIn(page: Page) {
  const creds = loadProdCreds();
  await page.goto('/login');
  await page.getByText('Log in', { exact: true }).click();
  await page.locator('input[type="email"]').fill(creds.email);
  const pw = page.locator('input[type="password"]');
  await pw.fill(creds.password);
  await pw.locator('..').locator('xpath=following-sibling::button[1]').click();
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 30_000 });
}

const at = (p: Page) => p.locator('video').evaluate((v: HTMLVideoElement) => v.currentTime);

test('an in-place re-initialisation keeps the viewer in place', async ({ browser }) => {
  const ctx = await browser.newContext();
  try {
    const page = await ctx.newPage();
    await signIn(page);
    await page.goto(`/team/personal/resources/file/${RID}`);
    await expect(page.locator('video')).toBeVisible({ timeout: 30_000 });
    await page.waitForTimeout(4000);

    await page.locator('video').evaluate(async (v: HTMLVideoElement) => {
      v.currentTime = 90;
      await new Promise((r) => v.addEventListener('seeked', r, { once: true }));
      await v.play();
    });
    await page.waitForTimeout(2500);
    await page.locator('video').evaluate((v: HTMLVideoElement) => v.pause());
    await page.waitForTimeout(2000);
    const before = await at(page);
    console.log('BEFORE=' + before);

    // Reproduce exactly what the reported failure was: the element is
    // re-initialised in place (`load()` re-runs the resource selection the
    // same way a changed src/token does) while the page keeps running.
    await page.locator('video').evaluate((v: HTMLVideoElement) => v.load());
    await page.waitForTimeout(6000);

    const after = await at(page);
    console.log('AFTER=' + after);
    expect(after, 'position must survive an in-place re-initialisation').toBeGreaterThan(60);

    // And a real tab switch, for the reported gesture end to end.
    const other = await ctx.newPage();
    await other.goto('https://example.com');
    await other.bringToFront();
    await other.waitForTimeout(5000);
    await page.bringToFront();
    await page.waitForTimeout(5000);
    console.log('AFTER_TAB_SWITCH=' + (await at(page)));
    await other.close();
  } finally {
    await ctx.close();
  }
});
