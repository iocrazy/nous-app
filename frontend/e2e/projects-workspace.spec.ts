import { test, expect, type Page, type Route } from '@playwright/test';
import { setupStubbedSession, TEAM_ID } from './helpers/stubs';
import { realSceneRow, realShotRow } from './helpers/realShapes';

/**
 * Visual regression stub spec for the PR-10b workspace shell (Wave 2):
 * Episodes management module, Characters/Locations main-library module,
 * and the Files module's Renders chip — against
 * docs/superpowers/specs/2026-07-10-projects-workspace-final.html.
 *
 * Same network-stub approach as projects-phase-b.spec.ts: MediaHub has no
 * runnable dev backend for e2e, so `/api/v1/projects*` is intercepted with
 * deterministic fixtures on top of `setupStubbedSession`'s catch-alls.
 * The detail pane is always the ProjectWorkspace shell — it is the only
 * project detail implementation (the legacy surface was retired in PR-18).
 */

const PROJECTS_URL = `/team/${TEAM_ID}/projects`;

const CATALOG = [
  { id: '1', slug: 'planning', name: 'Planning', sort_order: 1, tools_recommended: [] },
  { id: '2', slug: 'script', name: 'Script', sort_order: 2, tools_recommended: [] },
  { id: '3', slug: 'storyboard', name: 'Storyboard', sort_order: 3, tools_recommended: [] },
];

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
  latest_activity: { kind: 'file', actor: 'HG', at: '2026-07-08T10:00:00Z', stalled: false },
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

// The Characters module is now the project's view over the ASSET LIBRARY
// (P3 Task 5): `ProjectAssetsPanel` renders `AssetCard`s off
// GET /projects/{id}/assets, filtered by `?type=`. It replaced the
// `CharacterLibrary` bible-card wall over `project_characters`, which in turn
// had replaced the derived `/entities` list — the wall's `character-library` /
// `character-card` testids no longer exist anywhere in the tree.
//
// Real wire shape, not a tidied one (CLAUDE.md 边界 mock): every id is a
// STRING (the repository stringifies BIGINT so a Snowflake never lands in a JS
// number), `scope_id` is non-null because only global presets carry null,
// `tags` is an OBJECT of group → values rather than a flat array,
// `file_counts_by_slot` is sparse, and `readiness` / `project_ids` are
// server-derived rather than columns. `cover_file_id` is null on purpose so
// the cards draw their type glyph instead of fetching a portrait.
const PROJECT_ASSETS = [
  {
    id: '727145299382534300',
    scope_id: '727145299382534200',
    asset_type: 'character',
    subtype: null,
    name: 'CLIENT',
    role_tag: 'lead',
    description: 'The buyer, always on the phone.',
    attrs: {},
    prompt_positive: null,
    prompt_negative: null,
    prompt_positive_zh: null,
    prompt_negative_zh: null,
    platform_params: {},
    cover_file_id: null,
    source: 'script_import',
    duplicated_from: null,
    is_system_preset: false,
    tags: { role: ['lead'] },
    sort_order: 10,
    created_by: '11111111-1111-1111-1111-111111111111',
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
    readiness: { state: 'ready', missing: [] },
    file_counts_by_slot: { sheet: 1 },
    project_ids: ['1'],
    loadout_count: 0,
  },
  {
    id: '727145299382534301',
    scope_id: '727145299382534200',
    asset_type: 'character',
    subtype: null,
    name: 'DEV',
    role_tag: 'support',
    description: 'The engineer who ships.',
    attrs: {},
    prompt_positive: null,
    prompt_negative: null,
    prompt_positive_zh: null,
    prompt_negative_zh: null,
    platform_params: {},
    cover_file_id: null,
    source: 'script_import',
    duplicated_from: null,
    is_system_preset: false,
    tags: {},
    sort_order: 20,
    created_by: '11111111-1111-1111-1111-111111111111',
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
    // Draft rather than ready on the second card: the readiness chip and the
    // coverage ring are the two things `AssetCard` derives, and a fixture
    // where every row is identical could not tell a broken derivation from a
    // working one.
    readiness: { state: 'draft', missing: ['sheet'] },
    file_counts_by_slot: {},
    project_ids: ['1'],
    loadout_count: 0,
  },
];

// generated_media ids are Snowflake BIGINTs stringified on the way out
// (`_normalize` in generated_media_repository) — NOT the tidy 'r1' this
// fixture used to carry. `mediaUrl`'s two-tier helpers only recognise
// `/generated-media/<digits>/cover`, so a made-up id makes `mediaSrc` no-op
// and this spec would prove nothing about the render thumbnail. Kept in step
// with components/workspace/WorkspaceFiles.test.tsx.
const R_IMAGE = '7412093847562137600';
const R_VIDEO = '7412093847562137601';
const RENDERS = {
  items: [
    { id: R_IMAGE, media_kind: 'image', mime: 'image/png', origin_kind: 'shot_generate', node_id: 's1', created_at: '2026-07-08T00:00:00Z' },
    { id: R_VIDEO, media_kind: 'video', mime: 'video/mp4', origin_kind: 'shot_video', node_id: 's2', created_at: '2026-07-08T00:00:00Z' },
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

// A single-node workflow whose CURRENT node is storyboard-surfaced — drives
// the standalone Storyboard module page (三视图主工作面, PR-A; lifted out of
// Overview into its own module by IA redesign Task 2). Only used by the test
// below that exercises it; the other tests in this file never stub
// `/workflow` and keep getting the generic `{success:true,data:[]}` catch-all
// from setupStubbedSession (has_workflow undefined → WorkflowSection's empty
// "Set up workflow" CTA — unrelated pre-existing behavior, unaffected here).
const WORKFLOW_NODE = {
  id: 'wn1',
  project_id: '1',
  source_template_node_id: null,
  legacy_stage_id: null,
  name: 'Storyboard',
  sort_order: 0,
  parallel_group: null,
  status: 'in_progress',
  owner_user_id: null,
  owner_agent_id: null,
  planned_start: null,
  planned_due: null,
  review_required: false,
  deliverable_required: false,
  deliverable_label: null,
  skipped: false,
  surface: 'storyboard',
  members: [],
  completion_policy: 'owner',
  events: { notify_on_arrival: true, notify_on_complete: false, suggest_agent_run: false },
};
const WORKFLOW = { has_workflow: true, current_node_id: 'wn1', agents_active: 0, nodes: [WORKFLOW_NODE] };

// One scene (Ep 1's script s1) with one shot — feeds EpisodeSceneBoard /
// EpisodeShotListTable, both of which self-fetch via /scripts/{id}/scenes and
// /scenes/{id}/shots (editor/sceneService.ts). Real wire shape (realShapes.ts,
// CLAUDE.md「边界 mock 必须用真实 JSON 形状」): scene/shot `id` and the FK
// `scene_id` are JSON NUMBERS on the wire — this repo's own e2e/unit fixtures
// used idealized strings ('300') everywhere until the 2026-08-12 dup-id
// incident, which is exactly why that mismatch shipped undetected.
const SCENE_ID = 208443000000300;
const SHOT_ID = 208443000000400;
const SCENES = [
  realSceneRow({
    id: SCENE_ID,
    locationText: 'Kitchen',
    contentJson: [{ id: 'el1', type: 'action', text: 'She enters.' }],
  }),
];
const SHOTS_BY_SCENE: Record<string, unknown[]> = {
  [String(SCENE_ID)]: [
    realShotRow({
      id: SHOT_ID,
      sceneId: SCENE_ID,
      shotType: 'WIDE',
      cameraAngle: 'EYE',
      description: 'Establishing shot of the kitchen.',
      sortOrder: 0,
    }),
  ],
};

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
    if (pathname.endsWith('/episodes/progress')) return route.fulfill({ json: { success: true, data: EPISODES } });
    if (pathname.endsWith('/entities')) return route.fulfill({ json: { success: true, data: ENTITIES } });
    if (pathname.endsWith('/assets')) return route.fulfill({ json: { success: true, data: PROJECT_ASSETS } });
    if (pathname.endsWith('/renders')) return route.fulfill({ json: { success: true, data: RENDERS } });
    return route.fallback();
  });

  // Renders grid thumbnails — real bytes so the screenshot doesn't show a
  // broken-image icon for the image-kind render. The trailing `*` is
  // load-bearing: Playwright globs the FULL url including the query, and
  // `mediaSrc` stamps a `?v=2` cache bust on every /cover it emits.
  await page.route('**/api/v1/generated-media/*/cover*', (route) =>
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

/**
 * Stubs `/api/v1/projects/{id}/workflow` — a separate route from
 * `routeWorkspaceApi`'s catch-all (registered AFTER it in the tests that need
 * it, so Playwright's most-recently-registered-wins ordering lets this one
 * answer `/workflow` specifically while `routeWorkspaceApi`'s broader
 * `/api/v1/projects*` regex keeps handling everything else unchanged).
 * `fetchProjectWorkflow` (workflowService.ts) does NOT use the `{data}`
 * envelope — the response body IS the ProjectWorkflow object directly.
 */
async function routeWorkflowApi(page: Page): Promise<void> {
  await page.route(/\/api\/v1\/projects\/[^/]+\/workflow(\?|$)/, (route: Route) =>
    route.fulfill({ json: WORKFLOW }),
  );
}

/**
 * Stubs the v2 editor's scene/shot endpoints (editor/sceneService.ts) that
 * EpisodeSceneBoard / EpisodeShotListTable self-fetch (三视图主工作面, PR-A
 * Task 3) — separate from `routeScriptsApi`'s `/api/v1/scripts/projects*`
 * surface (script list/detail), a disjoint path shape under the same
 * `/api/v1/scripts` prefix.
 */
async function routeSceneShotsApi(page: Page): Promise<void> {
  await page.route(/\/api\/v1\/scripts\/[^/]+\/scenes(\?|$)/, (route: Route) =>
    route.fulfill({ json: { success: true, data: SCENES } }),
  );
  await page.route(/\/api\/v1\/scenes\/[^/]+\/shots(\?|$)/, (route: Route) => {
    const { pathname } = new URL(route.request().url());
    const match = pathname.match(/^\/api\/v1\/scenes\/([^/]+)\/shots$/);
    const sceneId = match?.[1] ?? '';
    return route.fulfill({ json: { success: true, data: SHOTS_BY_SCENE[sceneId] ?? [] } });
  });
  await page.route(/\/api\/v1\/scenes\/[^/]+\/auto-storyboard$/, (route: Route) =>
    route.fulfill({ json: { success: true, task_id: 'e2e-auto-task' } }),
  );
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

    // Episodes management module — opening it also EXPANDS the sidebar episode
    // tree (collapsed by default on Overview), which the ep-card switcher below
    // depends on. Shows the full row list, not just the current ep.
    await page.getByTestId('ws-module-episodes').click();
    await expect(page.getByTestId('ws-episodes')).toBeVisible();
    await expect(page.getByTestId('ws-episode-row-1')).toContainText('Ep 1 — Pilot');
    await expect(page.getByTestId('ws-episode-row-2')).toContainText('Ep 2 — Cutdown');
    await expect(page.getByTestId('ws-episode-progress-1')).toContainText('9/12');

    // Ep switch via the sidebar's compact popover (tree now expanded).
    await expect(page.getByTestId('ws-ep-card')).toHaveText(/Ep 1 — Pilot/);
    await page.getByTestId('ws-ep-card').click();
    await page.getByTestId('ws-ep-option-2').click();
    await expect(page.getByTestId('ws-ep-card')).toHaveText(/Ep 2 — Cutdown/);

    // Characters module — the project's view over the asset library (P3).
    // Asserted on VISIBILITY, not `toHaveCount` alone: the storyboard-canvas
    // incident shipped duplicate nodes that existed in the DOM but were
    // permanently `visibility:hidden`, and a bare count passed on the broken
    // build (CLAUDE.md 前端上线验收).
    await page.getByTestId('ws-module-characters').click();
    await expect(page.getByTestId('project-assets-panel')).toBeVisible();
    await expect(page.getByTestId('asset-card').first()).toBeVisible();
    await expect(page.getByTestId('asset-card')).toHaveCount(2);
    await expect(page.getByTestId('asset-card').first()).toContainText('CLIENT');
    // The empty state and the load-error line are the two things this panel
    // must NOT be showing when it has rows — they are distinct elements, and
    // either one appearing here would mean the grid above came from somewhere
    // other than the stubbed response.
    await expect(page.getByTestId('project-assets-empty')).toHaveCount(0);
    await expect(page.getByTestId('project-assets-error')).toHaveCount(0);
    // Readiness is DERIVED per row (`ready` iff the primary slot holds a
    // file), so the two fixture rows must disagree — one chip of each.
    await expect(page.getByTestId('asset-card').first()).toHaveAttribute(
      'data-readiness',
      'ready',
    );
    await expect(page.getByTestId('asset-card').nth(1)).toHaveAttribute(
      'data-readiness',
      'draft',
    );

    // Costumes — the fourth ASSETS module, which had no page before P3. The
    // stub answers the same rows for every `?type=`, so this asserts the
    // module is REACHABLE and renders the same panel, not that it filters.
    await page.getByTestId('ws-module-costumes').click();
    await expect(page.getByTestId('project-assets-panel')).toBeVisible();

    // Files module — Renders chip consumes the /renders endpoint.
    await page.getByTestId('ws-module-files').click();
    await expect(page.getByTestId('ws-files')).toBeVisible();
    await page.getByTestId('ws-files-chip-renders').click();
    await expect(page.getByTestId(`ws-files-item-render-${R_IMAGE}`)).toBeVisible();
    await expect(page.getByTestId(`ws-files-item-render-${R_VIDEO}`)).toBeVisible();

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
    await expect(page.getByTestId('project-assets-panel')).toBeVisible();
    await expect(page.getByTestId('asset-card').first()).toBeVisible();
    await expect(page.getByTestId('asset-card').first()).toContainText('CLIENT');

    await page.getByTestId('ws-module-files').click();
    await page.getByTestId('ws-files-chip-renders').click();
    await expect(page.getByTestId(`ws-files-item-render-${R_IMAGE}`)).toBeVisible();

    await page.screenshot({ path: 'e2e-artifacts/projects-workspace-light.png', fullPage: true });
  });

  test('mounts the editor inline for Script and shows the disabled Publish child (PR-11)', async ({
    page,
  }) => {
    await routeWorkspaceApi(page);
    await routeScriptsApi(page);
    await page.goto(`${PROJECTS_URL}/1`);

    await expect(page.getByTestId('workspace-topbar')).toBeVisible({ timeout: 10_000 });

    // Expand the sidebar episode tree (collapsed by default on Overview) so the
    // ep-card and its Script/Storyboard children are reachable.
    await page.getByTestId('ws-module-episodes').click();
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

    // Publish placeholder (G12) — visible, disabled, no module content.
    await expect(page.getByTestId('ws-ep-publish')).toBeVisible();
    await expect(page.getByTestId('ws-ep-publish')).toBeDisabled();
  });

  test('storyboard child lands on the standalone Storyboard module page (scene board + shot list), not the embedded editor (IA redesign Task 2)', async ({
    page,
  }) => {
    await routeWorkspaceApi(page);
    await routeScriptsApi(page);
    await routeWorkflowApi(page);
    await routeSceneShotsApi(page);
    await page.goto(`${PROJECTS_URL}/1`);

    await expect(page.getByTestId('workspace-topbar')).toBeVisible({ timeout: 10_000 });
    await page.getByTestId('ws-module-episodes').click();
    await expect(page.getByTestId('ws-ep-card')).toHaveText(/Ep 1 — Pilot/);

    // Storyboard child — 三视图主工作面 (PR-A), promoted to its OWN standalone
    // module page by IA redesign Task 2: the sidebar's 分镜 row now routes
    // straight to EpisodeStoryboardPage (activeModule='storyboard'), not back
    // onto Overview's old surface panel (which Task 2 removed) and not the
    // embedded editor. Task 3 then re-laid the scene board out as fixed-width
    // columns (`scene-column-*`, replacing the old full-width `ep-scene-card-*`
    // rows).
    await page.getByTestId('ws-ep-storyboard').click();
    await expect(page.getByTestId('episode-storyboard-page')).toBeVisible();
    await expect(page.getByTestId('ws-overview')).toHaveCount(0);
    await expect(page.getByTestId('episode-view-tabs')).toBeVisible();
    await expect(page.getByTestId(`scene-column-${SCENE_ID}`)).toBeVisible();
    await expect(page.locator('[data-editor-shell]')).toHaveCount(0);

    // Shot List tab — the flat per-shot table for the same script.
    await page.locator('[data-view="shotlist"]').click();
    await expect(page.getByTestId('ep-shotlist-table')).toBeVisible();
    await expect(page.getByTestId(`ep-shotlist-row-${SHOT_ID}`)).toBeVisible();

    // Publish placeholder (G12) — still visible/disabled regardless of which
    // surface view is active (it's the sidebar tree, not the content pane).
    await expect(page.getByTestId('ws-ep-publish')).toBeVisible();
    await expect(page.getByTestId('ws-ep-publish')).toBeDisabled();
  });
});
