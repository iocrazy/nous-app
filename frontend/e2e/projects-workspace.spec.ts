import { test, expect, type Page, type Route } from '@playwright/test';
import { setupStubbedSession, TEAM_ID } from './helpers/stubs';

/**
 * Visual regression stub spec for the PR-10b workspace shell (Wave 2):
 * Episodes management module, Characters/Locations main-library module,
 * and the Files module's Renders chip — against
 * docs/superpowers/specs/2026-07-10-projects-workspace-final.html.
 *
 * Same network-stub approach as projects-phase-b.spec.ts: MediaHub has no
 * runnable dev backend for e2e, so `/api/v1/projects*` is intercepted with
 * deterministic fixtures on top of `setupStubbedSession`'s catch-alls.
 * VITE_FEATURE_PROJECT_WORKSPACE_V2 is baked true in playwright.config.ts's
 * webServer build, so the detail pane is always the new ProjectWorkspace
 * shell for these tests (no per-test flag toggle needed).
 */

const PROJECTS_URL = `/team/${TEAM_ID}/projects`;

const CATALOG = [
  { id: '1', slug: 'planning', name: 'Planning', sort_order: 1, tools_recommended: [] },
  { id: '2', slug: 'script', name: 'Script', sort_order: 2, tools_recommended: [] },
  { id: '3', slug: 'storyboard', name: 'Storyboard', sort_order: 3, tools_recommended: [] },
];

const CURRENT_STAGE = { id: '3', slug: 'storyboard', name: 'Storyboard', sort_order: 3, tools_recommended: [] };

const PROJECT = {
  id: '1',
  name: 'Spring Campaign 2026',
  description: 'Short-form ad series',
  owner_id: 'u1',
  team_id: TEAM_ID,
  project_type: 'external',
  project_group: null,
  announcement: null,
  is_starred: false,
  color_label: null,
  archived_at: null,
  file_count: 128,
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-07-08T10:00:00Z',
  current_stage: { slug: 'storyboard', name: 'Storyboarding', index: 3, total: 3 },
  latest_activity: { kind: 'file', actor: 'HG', at: '2026-07-08T10:00:00Z', stalled: false },
};

const SUGGESTION = {
  stage_slug: 'storyboard',
  kind: 'storyboard_generate',
  progress: { total: 12, done: 9, empty: 3, generating: 0, failed: 0, script_count: 2, scene_count: 5 },
  action: { type: 'generate_missing_frames', label_key: 'projects.suggest.ctaGenerate', count: 3 },
};

const EPISODES = [
  {
    episode_id: '1',
    title: 'Ep 1 — Pilot',
    sort_order: 10,
    script_count: 1,
    scene_count: 4,
    shots_total: 12,
    shots_done: 9,
    renders_count: 1,
    status: 'boarding',
  },
  {
    episode_id: '2',
    title: 'Ep 2 — Cutdown',
    sort_order: 20,
    script_count: 1,
    scene_count: 2,
    shots_total: 4,
    shots_done: 0,
    renders_count: 0,
    status: 'drafting',
  },
];

const ENTITIES = {
  characters: [
    { name: 'CLIENT', cue_count: 12, episode_ids: ['1', '2'] },
    { name: 'DEV', cue_count: 7, episode_ids: ['1'] },
  ],
  locations: [{ name: 'Radio Booth', scene_count: 3, episode_ids: ['1'] }],
};

const RENDERS = {
  items: [
    { id: 'r1', media_kind: 'image', mime: 'image/png', origin_kind: 'shot_generate', node_id: 's1', created_at: '2026-07-08T00:00:00Z' },
    { id: 'r2', media_kind: 'video', mime: 'video/mp4', origin_kind: 'shot_video', node_id: 's2', created_at: '2026-07-08T00:00:00Z' },
  ],
  next_cursor: null,
};

const PNG_BASE64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEUlEQVR4nGP8z8Dwn4EIwAhUBADtgwXqWkAdRgAAAABJRU5ErkJggg==';

// One script on Ep 1 (the default current episode) — resolved by
// ProjectWorkspace's Script/Storyboard sidebar children (fetchScriptProjects
// list) and then loaded by the inline-mounted EditorShell (fetchScriptProject
// detail). See routeScriptsApi below for the depth-choice rationale.
const SCRIPTS = [
  {
    id: 's1',
    name: 'Pilot Draft',
    status: 'active',
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-08T00:00:00Z',
    episode_id: '1',
  },
];

/**
 * Installs the single `**\/api/v1/projects*` handler that dispatches on
 * pathname to the fixtures above. A RegExp on the pathname (rather than a
 * glob) matches every depth uniformly — see projects-phase-b.spec.ts's
 * routeProjectsApi for why a plain `**\/api/v1/projects*` glob is unsafe.
 */
async function routeWorkspaceApi(page: Page): Promise<void> {
  await page.route(/\/api\/v1\/projects(\/|\?|$)/, (route: Route) => {
    const { pathname } = new URL(route.request().url());

    if (pathname === '/api/v1/projects') return route.fulfill({ json: { data: [PROJECT] } });
    if (pathname === '/api/v1/projects/stages/catalog') return route.fulfill({ json: { data: CATALOG } });
    if (pathname.endsWith('/current_stage')) return route.fulfill({ json: { data: CURRENT_STAGE } });
    if (pathname.endsWith('/stage-suggestion')) return route.fulfill({ json: SUGGESTION });
    if (pathname.endsWith('/episodes/progress')) return route.fulfill({ json: { success: true, data: EPISODES } });
    if (pathname.endsWith('/entities')) return route.fulfill({ json: { success: true, data: ENTITIES } });
    if (pathname.endsWith('/renders')) return route.fulfill({ json: { success: true, data: RENDERS } });
    return route.fallback();
  });

  // Renders grid thumbnails — real bytes so the screenshot doesn't show a
  // broken-image icon for the image-kind render.
  await page.route('**/api/v1/generated-media/*/cover', (route) =>
    route.fulfill({ status: 200, contentType: 'image/png', body: Buffer.from(PNG_BASE64, 'base64') }),
  );
}

/**
 * Stubs the `/api/v1/scripts*` surface the inline-mounted editor touches on
 * boot (PR-11): the list endpoint ProjectWorkspace uses to resolve the
 * current episode's script, and the detail endpoint EditorShell uses to
 * load its owning project/episode id. Everything else the shell fetches on
 * mount (scenes, episodes-for-project, commit history) is left to
 * `setupStubbedSession`'s `**\/api/v1/**` catch-all, which returns
 * `{success:true,data:[]}` — an empty-but-valid response every one of those
 * call sites already tolerates (empty scene list → the shell's own
 * ColdStart empty state, not a crash). Depth choice: this proves the
 * INLINE MOUNT (no route jump, shell renders, storyboard view presets
 * correctly) without re-implementing the full scene-editing e2e surface
 * that EditorShell.test.tsx and the editor's own e2e specs already cover.
 */
async function routeScriptsApi(page: Page): Promise<void> {
  await page.route(/\/api\/v1\/scripts(\/|\?|$)/, (route: Route) => {
    const { pathname } = new URL(route.request().url());

    if (pathname === '/api/v1/scripts/projects') {
      return route.fulfill({ json: { success: true, data: { items: SCRIPTS, total: SCRIPTS.length } } });
    }
    const detail = pathname.match(/^\/api\/v1\/scripts\/projects\/([^/]+)$/);
    if (detail) {
      return route.fulfill({
        json: {
          success: true,
          data: {
            project: { id: detail[1], project_id: PROJECT.id, episode_id: '1', name: 'Pilot Draft' },
            chapters: [],
          },
        },
      });
    }
    return route.fallback();
  });
}

test.describe('Projects workspace shell — PR-10b Wave 2 modules', () => {
  test.beforeEach(async ({ page }) => {
    await setupStubbedSession(page);
    await page.addInitScript(() => {
      try {
        localStorage.setItem('language', 'en');
      } catch {
        /* localStorage unavailable — nothing we can do */
      }
    });
  });

  test('walks Episodes / Characters / Files-Renders modules — dark theme', async ({ page }) => {
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.addInitScript(() => {
      try {
        localStorage.setItem('mediahub.theme', 'dark');
      } catch {
        /* localStorage unavailable */
      }
    });
    await routeWorkspaceApi(page);
    await page.goto(`${PROJECTS_URL}/1`);

    // Shell: top bar + sidebar render.
    await expect(page.getByTestId('workspace-topbar')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId('workspace-sidebar')).toBeVisible();
    await expect(page.getByTestId('ws-ep-card')).toHaveText(/Ep 1 — Pilot/);

    // Ep switch via the sidebar's compact popover.
    await page.getByTestId('ws-ep-card').click();
    await page.getByTestId('ws-ep-option-2').click();
    await expect(page.getByTestId('ws-ep-card')).toHaveText(/Ep 2 — Cutdown/);

    // Episodes management module — full row list (not just the current ep).
    await page.getByTestId('ws-module-episodes').click();
    await expect(page.getByTestId('ws-episodes')).toBeVisible();
    await expect(page.getByTestId('ws-episode-row-1')).toContainText('Ep 1 — Pilot');
    await expect(page.getByTestId('ws-episode-row-2')).toContainText('Ep 2 — Cutdown');
    await expect(page.getByTestId('ws-episode-progress-1')).toContainText('9/12');

    // Characters main-library module.
    await page.getByTestId('ws-module-characters').click();
    await expect(page.getByTestId('ws-entities-characters')).toBeVisible();
    await expect(page.getByTestId('ws-entities-row-0')).toContainText('CLIENT');
    await expect(page.getByTestId('ws-entities-count-0')).toContainText('12');

    // Files module — Renders chip consumes the /renders endpoint.
    await page.getByTestId('ws-module-files').click();
    await expect(page.getByTestId('ws-files')).toBeVisible();
    await page.getByTestId('ws-files-chip-renders').click();
    await expect(page.getByTestId('ws-files-item-render-r1')).toBeVisible();
    await expect(page.getByTestId('ws-files-item-render-r2')).toBeVisible();

    await page.screenshot({ path: 'e2e-artifacts/projects-workspace-dark.png', fullPage: true });
  });

  test('workspace shell modules render — light theme', async ({ page }) => {
    await page.emulateMedia({ colorScheme: 'light' });
    await page.addInitScript(() => {
      try {
        localStorage.setItem('mediahub.theme', 'light');
      } catch {
        /* localStorage unavailable */
      }
    });
    await routeWorkspaceApi(page);
    await page.goto(`${PROJECTS_URL}/1`);

    await expect(page.getByTestId('workspace-topbar')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId('workspace-sidebar')).toBeVisible();

    await page.getByTestId('ws-module-episodes').click();
    await expect(page.getByTestId('ws-episode-row-1')).toContainText('Ep 1 — Pilot');

    await page.getByTestId('ws-module-characters').click();
    await expect(page.getByTestId('ws-entities-row-0')).toContainText('CLIENT');

    await page.getByTestId('ws-module-files').click();
    await page.getByTestId('ws-files-chip-renders').click();
    await expect(page.getByTestId('ws-files-item-render-r1')).toBeVisible();

    await page.screenshot({ path: 'e2e-artifacts/projects-workspace-light.png', fullPage: true });
  });

  test('mounts the editor inline for Script/Storyboard and shows the disabled Publish child (PR-11)', async ({
    page,
  }) => {
    await routeWorkspaceApi(page);
    await routeScriptsApi(page);
    await page.goto(`${PROJECTS_URL}/1`);

    await expect(page.getByTestId('workspace-topbar')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId('ws-ep-card')).toHaveText(/Ep 1 — Pilot/);

    // Script child — inline mount, no route jump (URL stays on the project).
    await page.getByTestId('ws-ep-script').click();
    await expect(page.locator('[data-editor-shell]')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole('main', { name: 'Script Page' })).toBeVisible();
    expect(new URL(page.url()).pathname).toBe(`${PROJECTS_URL}/1`);
    // The workspace sidebar (project-level nav) stays mounted alongside the
    // editor's own rail (script-level nav) — Studio-frame convergence.
    await expect(page.getByTestId('workspace-sidebar')).toBeVisible();
    await expect(page.getByTestId('ws-overview')).toHaveCount(0);

    // Storyboard child — same shell, preset to the storyboard rail view
    // (scoped to the editor's own RailModules nav, not the sidebar child
    // button or the top-bar stage stepper's storyboard dot, which share
    // the same accessible name).
    await page.getByTestId('ws-ep-storyboard').click();
    await expect(page.locator('[data-editor-shell]')).toBeVisible();
    await expect(
      page.getByTestId('ws-script-editor').getByRole('button', { name: 'Storyboard' }),
    ).toHaveAttribute('aria-current', 'page');

    // Publish placeholder (G12) — visible, disabled, no module content.
    await expect(page.getByTestId('ws-ep-publish')).toBeVisible();
    await expect(page.getByTestId('ws-ep-publish')).toBeDisabled();
  });
});
