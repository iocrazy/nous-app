import { test } from '@playwright/test';
import { loadProdCreds } from './helpers';
test('mint token', async ({ page }) => {
  test.setTimeout(120000);
  const creds = loadProdCreds();
  await page.goto('/login');
  await page.getByText('Log in', { exact: true }).click();
  await page.locator('input[type="email"]').fill(creds.email);
  const pw = page.locator('input[type="password"]');
  await pw.fill(creds.password);
  await pw.locator('..').locator('xpath=following-sibling::button[1]').click();
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 20000 });
  await page.waitForTimeout(2000);
  const tok = await page.evaluate(() => {
    const keys = Object.keys(localStorage).filter((k) => k.includes('auth-token'));
    const raw = keys.length ? localStorage.getItem(keys[0]) : null;
    return raw ? (JSON.parse(raw).access_token as string) : '';
  });
  console.log('JWTV=' + tok);
});
