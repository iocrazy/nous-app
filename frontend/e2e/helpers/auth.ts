import { type Page } from '@playwright/test';

export const E2E_EMAIL = process.env.E2E_EMAIL ?? '';
export const E2E_PASSWORD = process.env.E2E_PASSWORD ?? '';
export const hasAuth = Boolean(E2E_EMAIL && E2E_PASSWORD);

/** Log in via the AuthOverlay (email + password). Leaves the app on a
 *  post-login route. No-op-safe to call once per test in beforeEach. */
export async function loginViaUI(page: Page): Promise<void> {
  await page.goto('/login');
  const trigger = page.getByRole('button', { name: /log in|get started/i });
  if (await trigger.isVisible().catch(() => false)) await trigger.click();
  await page.getByPlaceholder(/email/i).fill(E2E_EMAIL);
  await page.getByPlaceholder(/password/i).fill(E2E_PASSWORD);
  await page.getByRole('button', { name: /sign in|log in/i }).click();
  await page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 15_000 });
}
