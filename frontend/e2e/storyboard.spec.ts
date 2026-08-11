import { test, expect } from '@playwright/test';

// NOTE: this file used to cover the standalone ReactFlow "Storyboard Workbench"
// editor (`/team/:teamId/projects/:projectId/storyboard/:storyboardId`,
// toolbar toggles for script/timeline/characters/chat/export, a seeded
// generate-node canvas flow, etc). That route was retired in the Phase B P4
// cutover (commit f137bf08, PR #1491, see `pages/StoryboardWorkbench/index.tsx`
// — `StoryboardMovedRedirect`): it unconditionally redirects to the project's
// Scripts tab now, since storyboarding moved into the script editor as the
// per-scene shot board. None of the `sb-toggle-*` / `sb-panel-*` /
// `sb-export-dialog` / `sb-script-dialog` / `sb-generate` / `sb-result-image`
// testids this file asserted on ever existed anywhere outside this spec —
// the whole `editor` + `creative flow (seeded canvas)` suites were testing a
// UI shape the product never shipped at this route. The replacement surface
// (episode storyboard page + per-scene shot board) already has its own e2e
// coverage in `projects-workspace.spec.ts`. Deleted rather than rewritten;
// see `.superpowers/e2e-cleanup-report.md` for the full accounting.
test.describe('Storyboard Workbench (stubbed backend)', () => {
  test('login page renders', async ({ page }) => {
    await page.goto('/login');
    await expect(page.getByRole('button', { name: 'Get Started' })).toBeVisible();
  });
});
