import { test, expect } from '@playwright/test';
import {
  EDITOR_URL,
  GEN_FLOW_NODES,
  setupGenerationStubs,
  setupStubbedSession,
} from './helpers/stubs';

test.describe('Storyboard Workbench (stubbed backend)', () => {
  test('login page renders', async ({ page }) => {
    await page.goto('/login');
    await expect(page.getByRole('button', { name: 'Get Started' })).toBeVisible();
  });

  test.describe('editor', () => {
    test.beforeEach(async ({ page }) => {
      await setupStubbedSession(page);
      await page.goto(EDITOR_URL);
      // Editor mounted once the toolbar renders (project load resolved, no error).
      await expect(page.getByTestId('sb-toggle-export')).toBeVisible({ timeout: 15_000 });
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

  // ── Creative path: seed a graph (upload + storyboard_gen) via the stubbed
  //    project, then drive the generate job (submit + DBOS poll both stubbed).
  //    Split is driven through a hover→toolbar→dialog→client-processor chain
  //    that isn't reliably pinnable from outside; it's left to the real-stack
  //    suite (see spec). Export open/close is covered above.
  test.describe('creative flow (seeded canvas)', () => {
    test.beforeEach(async ({ page }) => {
      await setupStubbedSession(page, { nodes: GEN_FLOW_NODES });
      await setupGenerationStubs(page);
      await page.goto(EDITOR_URL);
      await expect(page.getByTestId('sb-toggle-export')).toBeVisible({ timeout: 15_000 });
    });

    test('seeded backend nodes map and render on the canvas', async ({ page }) => {
      // storyboard_gen → storyboardGenNode with its Generate button, proving the
      // node-type map + data_json passthrough; upload + gen = two canvas nodes.
      await expect(page.getByTestId('sb-generate')).toBeVisible({ timeout: 10_000 });
      await expect(page.locator('.react-flow__node')).toHaveCount(2);
    });

    test('generate yields a result image via stubbed job + poll', async ({ page }) => {
      await page.getByTestId('sb-generate').click();
      // submit → a new export-result node (isGenerating) → poll SUCCESS →
      // the fixture image renders in that node. .first() guards against a
      // future multi-result generate triggering a strict-mode violation.
      await expect(page.getByTestId('sb-result-image').first()).toBeVisible({ timeout: 20_000 });
    });
  });
});
