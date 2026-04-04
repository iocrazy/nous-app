import { test, expect, type Page } from '@playwright/test';

// ─── Helpers ────────────────────────────────────────────────────────────────

const E2E_EMAIL = process.env.E2E_EMAIL ?? '';
const E2E_PASSWORD = process.env.E2E_PASSWORD ?? '';
const hasAuth = Boolean(E2E_EMAIL && E2E_PASSWORD);

/** Log in via the AuthOverlay (email + password form). */
async function loginViaUI(page: Page) {
  await page.goto('/login');
  const trigger = page.getByRole('button', { name: /log in|get started/i });
  if (await trigger.isVisible()) await trigger.click();
  await page.getByPlaceholder(/email/i).fill(E2E_EMAIL);
  await page.getByPlaceholder(/password/i).fill(E2E_PASSWORD);
  await page.getByRole('button', { name: /sign in|log in/i }).click();
  await page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 15_000 });
}

/** Navigate to /projects, click first project card, wait for canvas editor. Returns false if no projects. */
async function navigateToEditor(page: Page): Promise<boolean> {
  await page.goto('/projects');
  const card = page.locator('[class*="ProjectCard"], [data-testid="project-card"]').first();
  const visible = await card.isVisible({ timeout: 8_000 }).catch(() => false);
  if (!visible) return false;
  await card.click();
  await page.getByRole('button', { name: /back/i }).waitFor({ timeout: 10_000 });
  return true;
}

// ─── Tests ──────────────────────────────────────────────────────────────────

test.describe('Storyboard Workbench', () => {
  test.describe('public pages', () => {
    test('login page renders', async ({ page }) => {
      await page.goto('/login');
      await expect(page.getByRole('button', { name: /log in|get started/i })).toBeVisible();
    });
  });

  test.describe('authenticated', () => {
    test.skip(!hasAuth, 'Skipped: E2E_EMAIL and E2E_PASSWORD env vars not set');

    test.beforeEach(async ({ page }) => {
      await loginViaUI(page);
    });

    test('navigate to projects page', async ({ page }) => {
      await page.goto('/projects');
      await expect(page.getByRole('button', { name: /new project/i })).toBeVisible({ timeout: 10_000 });
    });

    test('create storyboard shows modal', async ({ page }) => {
      await page.goto('/projects');
      await page.getByRole('button', { name: /new project/i }).click();
      const dialog = page.getByRole('dialog');
      await expect(dialog).toBeVisible({ timeout: 5_000 });
      await expect(dialog.locator('input')).toBeVisible();
      const cancelBtn = dialog.getByRole('button', { name: /cancel/i });
      if (await cancelBtn.isVisible()) await cancelBtn.click();
    });

    test('storyboard canvas editor loads', async ({ page }) => {
      const opened = await navigateToEditor(page);
      test.skip(!opened, 'No existing projects to open');
      await expect(page.getByRole('button', { name: /back/i })).toBeVisible();
    });

    test('canvas toolbar has all buttons', async ({ page }) => {
      const opened = await navigateToEditor(page);
      test.skip(!opened, 'No existing projects to open');
      for (const label of ['Script', 'Timeline', 'Characters', 'Chat', 'Export']) {
        await expect(page.getByRole('button', { name: new RegExp(label, 'i') })).toBeVisible();
      }
    });

    test('character panel opens and closes', async ({ page }) => {
      const opened = await navigateToEditor(page);
      test.skip(!opened, 'No existing projects to open');
      await page.getByRole('button', { name: /characters/i }).click();
      await expect(page.locator('.w-80.border-l')).toBeVisible({ timeout: 3_000 });
      await page.getByRole('button', { name: /characters/i }).click();
      await expect(page.locator('.w-80.border-l')).toBeHidden({ timeout: 3_000 });
    });

    test('chat panel opens and closes', async ({ page }) => {
      const opened = await navigateToEditor(page);
      test.skip(!opened, 'No existing projects to open');
      await page.getByRole('button', { name: /chat/i }).click();
      await expect(page.locator('.w-80.border-l')).toBeVisible({ timeout: 3_000 });
      await page.getByRole('button', { name: /chat/i }).click();
      await expect(page.locator('.w-80.border-l')).toBeHidden({ timeout: 3_000 });
    });

    test('timeline opens and closes', async ({ page }) => {
      const opened = await navigateToEditor(page);
      test.skip(!opened, 'No existing projects to open');
      const btn = page.getByRole('button', { name: /timeline/i });
      await btn.click();
      await expect(btn).toHaveClass(/indigo/);
      await btn.click();
      await expect(btn).not.toHaveClass(/indigo/);
    });

    test('export dialog opens and closes', async ({ page }) => {
      const opened = await navigateToEditor(page);
      test.skip(!opened, 'No existing projects to open');
      await page.getByRole('button', { name: /export/i }).click();
      const dialog = page.getByRole('dialog');
      await expect(dialog).toBeVisible({ timeout: 3_000 });
      await page.keyboard.press('Escape');
      await expect(dialog).toBeHidden({ timeout: 3_000 });
    });

    test('script import dialog opens and closes', async ({ page }) => {
      const opened = await navigateToEditor(page);
      test.skip(!opened, 'No existing projects to open');
      await page.getByRole('button', { name: /script/i }).click();
      const dialog = page.getByRole('dialog');
      await expect(dialog).toBeVisible({ timeout: 3_000 });
      await page.keyboard.press('Escape');
      await expect(dialog).toBeHidden({ timeout: 3_000 });
    });
  });
});
