import { test, expect } from '@playwright/test';
import { hasAuth, loginViaUI } from './helpers/auth';
import { createStoryboardProject, deleteProject } from './helpers/project';

test.describe('Storyboard Workbench', () => {
  test('login page renders', async ({ page }) => {
    await page.goto('/login');
    await expect(page.getByRole('button', { name: /log in|get started/i })).toBeVisible();
  });

  test.describe('editor (self-created project)', () => {
    test.skip(!hasAuth, 'Set E2E_EMAIL + E2E_PASSWORD to run authed E2E');

    let projectName = '';
    test.beforeEach(async ({ page }, testInfo) => {
      await loginViaUI(page);
      const created = await createStoryboardProject(page, testInfo.workerIndex * 1_000_000 + (Date.now() % 1_000_000));
      projectName = created.projectName;
    });
    test.afterEach(async ({ page }) => {
      await deleteProject(page, projectName);
    });

    test('toolbar exposes all panel toggles', async ({ page }) => {
      for (const id of ['script', 'timeline', 'characters', 'chat', 'export']) {
        await expect(page.getByTestId(`sb-toggle-${id}`)).toBeVisible();
      }
    });

    test('characters panel opens and closes', async ({ page }) => {
      await page.getByTestId('sb-toggle-characters').click();
      await expect(page.getByTestId('sb-panel-characters')).toBeVisible({ timeout: 3_000 });
      await page.getByTestId('sb-toggle-characters').click();
      await expect(page.getByTestId('sb-panel-characters')).toBeHidden({ timeout: 3_000 });
    });

    test('chat panel opens and closes', async ({ page }) => {
      // The chat toggle is a bottom-right FAB that hides itself while the
      // drawer is open, so close is driven by Escape (the drawer's own
      // close affordance), not a second toggle click.
      await page.getByTestId('sb-toggle-chat').click();
      await expect(page.getByTestId('sb-panel-chat')).toBeVisible({ timeout: 3_000 });
      await page.keyboard.press('Escape');
      await expect(page.getByTestId('sb-panel-chat')).toBeHidden({ timeout: 3_000 });
    });

    test('export dialog opens and closes', async ({ page }) => {
      // ExportDialog is a fixed overlay (not role="dialog").
      await page.getByTestId('sb-toggle-export').click();
      await expect(page.getByTestId('sb-export-dialog')).toBeVisible({ timeout: 3_000 });
      await page.keyboard.press('Escape');
      await expect(page.getByTestId('sb-export-dialog')).toBeHidden({ timeout: 3_000 });
    });

    test('script import dialog opens and closes', async ({ page }) => {
      // ScriptImportDialog is a fixed overlay (not role="dialog").
      await page.getByTestId('sb-toggle-script').click();
      await expect(page.getByTestId('sb-script-dialog')).toBeVisible({ timeout: 3_000 });
      await page.keyboard.press('Escape');
      await expect(page.getByTestId('sb-script-dialog')).toBeHidden({ timeout: 3_000 });
    });
  });
});
