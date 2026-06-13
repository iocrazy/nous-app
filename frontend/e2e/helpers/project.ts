import { type Page, expect } from '@playwright/test';

const PREFIX = 'E2E Storyboard';

export interface CreatedProject {
  projectName: string;
}

/** Create a project + a storyboard inside it, landing in the canvas editor.
 *  Two-level flow: New Project (CreateProjectModal) → project page →
 *  Storyboards tab → New Storyboard (CreateStoryboardModal) → editor.
 *  Returns the project name for cleanup.
 *
 *  Note: `/projects` redirects (RedirectToTeam) to `/team/:teamId/projects`,
 *  so the project + storyboard navigation always carries a real teamId. */
export async function createStoryboardProject(page: Page, ts: number): Promise<CreatedProject> {
  const projectName = `${PREFIX} ${ts}`;
  // 1. Project
  await page.goto('/projects');
  await page.getByTestId('new-project-btn').click();
  const pModal = page.getByTestId('create-project-modal');
  await expect(pModal).toBeVisible({ timeout: 5_000 });
  await pModal.getByTestId('project-name-input').fill(projectName);
  // CreateProjectModal submit button text is "Create" (i18n `common.create`,
  // becomes "Creating..." only while in-flight — match exact "Create").
  await pModal.getByRole('button', { name: /^create$/i }).click();
  await expect(pModal).toBeHidden({ timeout: 10_000 });
  // 2. Storyboards tab. After create the app is on /team/:teamId/projects/:projectId.
  //    ProjectsPage reads `?tab` from searchParams and auto-selects the project
  //    from the :projectId route param, so a hard nav with ?tab=storyboard
  //    reliably opens the Storyboards tab.
  await page.goto(page.url().split('?')[0] + '?tab=storyboard');
  await page.getByTestId('new-storyboard-btn').click();
  const sModal = page.getByTestId('create-storyboard-modal');
  await expect(sModal).toBeVisible({ timeout: 5_000 });
  await sModal.getByTestId('storyboard-name-input').fill(`${projectName} SB`);
  // CreateStoryboardModal submit button text is "Create" ("Creating..." in-flight).
  await sModal.getByRole('button', { name: /^create$/i }).click();
  // Editor is up when the Back button renders (text "Back to project" matches /back/i).
  await expect(page.getByRole('button', { name: /back/i })).toBeVisible({ timeout: 15_000 });
  return { projectName };
}

/** Best-effort cleanup: delete the PROJECT (cascades its storyboards).
 *  Never throws.
 *
 *  Real flow (grid view): hover the project-card → click the per-card menu
 *  button (project-menu-btn / MoreVertical) → ProjectContextMenu opens →
 *  click the "Delete Project" item → handleDeleteProject fires a native
 *  window.confirm(), which we auto-accept via a one-shot dialog handler. */
export async function deleteProject(page: Page, projectName: string): Promise<void> {
  try {
    await page.goto('/projects');
    const card = page.getByTestId('project-card').filter({ hasText: projectName }).first();
    if (!(await card.isVisible({ timeout: 5_000 }).catch(() => false))) return;
    await card.hover();
    const menuBtn = card.getByTestId('project-menu-btn');
    if (!(await menuBtn.isVisible().catch(() => false))) return;
    await menuBtn.click();
    // Auto-accept the window.confirm fired by the Delete Project click.
    page.once('dialog', (d) => { void d.accept().catch(() => {}); });
    const del = page.getByRole('button', { name: /delete project/i }).first();
    if (await del.isVisible({ timeout: 2_000 }).catch(() => false)) await del.click();
  } catch {
    // swallow — cleanup is best-effort
  }
}
