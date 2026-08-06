import { test, expect, type Page, type Route } from '@playwright/test';
import { setupStubbedSession, TEAM_ID, PARENT_PROJECT_ID, USER_ID } from './helpers/stubs';

/**
 * Pre-merge visual walkthrough for Project Workflow M3 PR-J (dependency
 * gates — task J3).
 *
 * Full-stub harness (same contract as workflow-walkthrough.spec.ts /
 * stage-board.spec.ts): no real backend, no login.
 *
 * Covers the three surfaces the design (§3) calls out:
 *   1. Editor — Node Info tab's "Depends on" multi-select. Selecting a
 *      candidate and Saving must PATCH a payload-INDEX depends_on (the
 *      template full-replace contract — see task-J1-report.md's "Consequence
 *      for J3"), never the loaded node id.
 *   2. AdvanceConfirmDialog — a stubbed DEPS_PENDING preview must render the
 *      server's `waiting_on` list verbatim (never a local derivation, #1400).
 *   3. WorkflowStrip — a lock icon on a current-group capsule whose
 *      dependency isn't done yet (local derivation, nodeStatus.ts::unmetDeps,
 *      display-only).
 *
 * Screenshots land in test-results/workflow/ for human review, alongside the
 * PR-C / PR-F walkthroughs' shots.
 */

const SHOTS = 'test-results/workflow';

function tplNode(id: string, name: string, sort: number, extra: Record<string, unknown> = {}) {
  return {
    id,
    template_id: 'tpl-1',
    name,
    sort_order: sort,
    parallel_group: null,
    default_owner_user_id: null,
    default_owner_agent_id: null,
    skip_default: false,
    review_required: false,
    deliverable_required: false,
    deliverable_label: null,
    source_stage_id: null,
    duration_days: null,
    members: [],
    completion_policy: 'owner',
    events: { notify_on_arrival: true, notify_on_complete: false, suggest_agent_run: false },
    form_schema: [],
    depends_on: [],
    ...extra,
  };
}

const TEMPLATE_LIST = [
  { id: 'tpl-1', team_id: TEAM_ID, name: 'Short-form', is_default: true, created_by: USER_ID, created_at: '2026-07-01T00:00:00Z', updated_at: '2026-07-01T00:00:00Z', node_count: 3 },
];

const TEMPLATE_DETAIL = {
  ...TEMPLATE_LIST[0],
  nodes: [
    tplNode('tn-1', 'Script', 0),
    tplNode('tn-2', 'Storyboard', 1),
    tplNode('tn-3', 'Editing', 2),
  ],
};

const PROJECT = {
  id: PARENT_PROJECT_ID,
  name: 'Deps Gate E2E Project',
  description: null,
  team_id: TEAM_ID,
  project_type: 'internal',
  project_group: null,
  is_starred: false,
  is_archived: false,
  file_count: 0,
  latest_activity: null,
  current_node_id: 'node-2',
  workflow_badge: {
    current_node_name: 'Storyboard',
    workflow_total: 3,
    workflow_position: 2,
    agents_active: 0,
  },
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-10T00:00:00Z',
};

function node(id: string, name: string, sort: number, status: string, extra: Record<string, unknown> = {}) {
  return {
    id,
    project_id: PARENT_PROJECT_ID,
    source_template_node_id: null,
    legacy_stage_id: null,
    name,
    sort_order: sort,
    parallel_group: null,
    status,
    owner_user_id: null,
    owner_agent_id: null,
    planned_start: null,
    planned_due: null,
    review_required: false,
    deliverable_required: false,
    deliverable_label: null,
    skipped: false,
    members: [],
    completion_policy: 'owner',
    events: { notify_on_arrival: true, notify_on_complete: false, suggest_agent_run: false },
    depends_on: [],
    ...extra,
  };
}

// node-2 (Storyboard, current) depends on node-1 (Script) — Script is still
// `in_progress`, so the dependency is unmet: WorkflowStrip must lock node-2's
// capsule, and the Stage Board must render "Waiting on: Script".
const WORKFLOW_LOCKED = {
  has_workflow: true,
  current_node_id: 'node-2',
  agents_active: 0,
  nodes: [
    node('node-1', 'Script', 0, 'in_progress'),
    node('node-2', 'Storyboard', 1, 'pending', { depends_on: ['node-1'] }),
    node('node-3', 'Editing', 2, 'pending'),
  ],
};

const STAGE_BOARD_DATA_LOCKED = {
  node: WORKFLOW_LOCKED.nodes[1],
  issue: null,
  files: [],
};

const DEPS_PENDING_PREVIEW = {
  direction: 'forward',
  will_advance: false,
  blocked_reason: 'DEPS_PENDING',
  closing: [],
  creating: [],
  warnings: [],
  missing_fields: [],
  waiting_on: ['Storyboard'],
};

// B2 #1712: one episode so ProjectWorkspace resolves a non-null
// `currentEpisodeId` — that's what makes the workflow read + advance chain ride
// `?episode_id=` on the query (and forces the workflow glob below to end in `*`).
const EPISODE_ID = 'ep-1';
const EPISODES_PROGRESS = [
  { episode_id: EPISODE_ID, title: 'Episode 1', sort_order: 0, script_count: 1, scene_count: 0, shots_total: 0, shots_done: 0, renders_count: 0, status: 'in_progress' },
];

function json(body: unknown) {
  return (route: Route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
}

async function setupTemplateEditorStubs(page: Page): Promise<void> {
  await setupStubbedSession(page);
  await page.route('**/api/v1/ai-library/agents', json([]));
  await page.route('**/api/v1/workflows?*', json({ success: true, data: TEMPLATE_LIST }));
  await page.route('**/api/v1/workflows/*', json({ success: true, data: TEMPLATE_DETAIL }));
  await page.route('**/api/v1/workflows/stage-library', json({ success: true, data: [] }));
}

/** Capture the template Save PATCH body — same idiom as
 * workflow-walkthrough.spec.ts's capturePatch. */
function capturePatch(page: Page): { get: () => Record<string, unknown> | null } {
  let body: Record<string, unknown> | null = null;
  void page.route('**/api/v1/workflows/*', async (route) => {
    const req = route.request();
    if (req.method() !== 'PATCH') {
      await route.fallback();
      return;
    }
    body = req.postDataJSON();
    const nodes = (body?.nodes as unknown[] | undefined) ?? TEMPLATE_DETAIL.nodes;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: { ...TEMPLATE_DETAIL, nodes } }),
    });
  });
  return { get: () => body };
}

async function setupWorkspaceStubs(page: Page): Promise<void> {
  await setupStubbedSession(page);
  await page.route('**/api/v1/ai-library/agents', json([]));
  await page.route('**/api/v1/projects?*', json({ success: true, data: [PROJECT] }));
  // Episodes progress (B2 #1712) — ≥1 episode so `currentEpisodeId` is non-null.
  await page.route('**/api/v1/projects/*/episodes/progress', json({ success: true, data: EPISODES_PROGRESS }));
  // Trailing `*` so the read still matches once it carries `?episode_id=`
  // (B2 #1712) — otherwise it falls through to the catch-all and the strip
  // renders empty.
  await page.route('**/api/v1/projects/*/workflow*', json(WORKFLOW_LOCKED));
  await page.route('**/api/v1/projects/*/advance-preview*', json(DEPS_PENDING_PREVIEW));
  // Registered AFTER the `/workflow*` glob above so this more-specific board
  // route stays in front of it (last-registered-wins; the `*` now overlaps it).
  await page.route(
    '**/api/v1/projects/*/workflow/nodes/*/board',
    json({ success: true, data: STAGE_BOARD_DATA_LOCKED }),
  );
}

async function forceTheme(page: Page, theme: 'dark' | 'light'): Promise<void> {
  await page.addInitScript((t) => {
    try {
      localStorage.setItem('mediahub.theme', t as string);
    } catch {
      /* ignore */
    }
  }, theme);
}

async function useEnglishLocale(page: Page): Promise<void> {
  await page.addInitScript(() => {
    try {
      localStorage.setItem('language', 'en');
    } catch {
      /* ignore */
    }
  });
}

async function openTemplateEditor(page: Page): Promise<void> {
  await page.goto(`/team/${TEAM_ID}/projects`);
  await page.getByTestId('workflow-templates-entry').click();
  await expect(page.getByTestId('workflow-template-editor')).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId('workflow-node-capsule').first()).toBeVisible();
}

async function openWorkspaceOverview(page: Page): Promise<void> {
  await page.goto(`/team/${TEAM_ID}/projects/${PARENT_PROJECT_ID}`);
  await expect(page.getByTestId('workflow-strip')).toBeVisible({ timeout: 15_000 });
}

for (const theme of ['dark', 'light'] as const) {
  test(`${theme}: editor "Depends on" — selecting a candidate Saves a payload-index depends_on (J1 contract)`, async ({ page }) => {
    await setupTemplateEditorStubs(page);
    const patch = capturePatch(page);
    await useEnglishLocale(page);
    await forceTheme(page, theme);
    await openTemplateEditor(page);

    // Select the second node (Storyboard) — Node Info is the default tab.
    await page.getByTestId('workflow-node-capsule').nth(1).click();

    // Only the earlier node (Script) is offered as a candidate — Editing
    // (positioned after Storyboard) must never appear.
    const candidates = page.getByTestId('workflow-dep-candidates');
    await expect(candidates).toBeVisible();
    const scriptCandidate = candidates.getByTestId('workflow-dep-candidate').filter({ hasText: 'Script' });
    await expect(scriptCandidate).toBeVisible();
    await expect(candidates.getByTestId('workflow-dep-candidate').filter({ hasText: 'Editing' })).toHaveCount(0);

    await expect(scriptCandidate).toHaveAttribute('aria-pressed', 'false');
    await scriptCandidate.click();
    await expect(scriptCandidate).toHaveAttribute('aria-pressed', 'true');
    await page.screenshot({ path: `${SHOTS}/15-deps-editor-select-${theme}.png`, fullPage: true });

    // Save → PATCH payload must carry the payload-INDEX contract: Script is
    // node[0] in the submitted array, so Storyboard's depends_on is ["0"],
    // never the (deleted-on-replace) loaded node id "tn-1".
    await page.getByTestId('workflow-save-template').click();
    await expect.poll(() => patch.get()).not.toBeNull();
    const body = patch.get() as { nodes: Array<Record<string, unknown>> };
    const storyboardNode = body.nodes.find((n) => n.name === 'Storyboard') as
      | { depends_on: string[] }
      | undefined;
    expect(storyboardNode).toBeDefined();
    expect(storyboardNode!.depends_on).toEqual(['0']);
  });

  test(`${theme}: editor "Depends on" excludes same-parallel-group siblings from candidates (M3 final review defense-in-depth)`, async ({ page }) => {
    // Regression for the depCandidates half of the Gate 5 same-group-deadlock
    // fix: Storyboard and Shotlist are parallel siblings (same
    // parallel_group) — Storyboard must never be offered as a dep candidate
    // for Shotlist even though it's positioned earlier in the draft array.
    // The backend now tolerates a same-group edge server-side (co-arrival
    // exemption), but the editor should still steer authors away from it —
    // offering it here is exactly what produced the deadlocking config.
    await setupTemplateEditorStubs(page);
    await useEnglishLocale(page);
    await forceTheme(page, theme);

    const PARALLEL_TEMPLATE_DETAIL = {
      ...TEMPLATE_DETAIL,
      nodes: [
        tplNode('tn-1', 'Script', 0),
        tplNode('tn-2', 'Storyboard', 1, { parallel_group: 1 }),
        tplNode('tn-2b', 'Shotlist', 2, { parallel_group: 1 }),
        tplNode('tn-3', 'Editing', 3),
      ],
    };
    await page.route(
      '**/api/v1/workflows/*',
      json({ success: true, data: PARALLEL_TEMPLATE_DETAIL }),
    );

    await openTemplateEditor(page);

    // Select Editing (last node, not part of the parallel group) — Script,
    // Storyboard, AND Shotlist are all valid candidates for it (none of them
    // share Editing's — null — parallel_group).
    await page.getByTestId('workflow-node-capsule').nth(3).click();
    const editingCandidates = page.getByTestId('workflow-dep-candidates');
    await expect(editingCandidates).toBeVisible();
    await expect(
      editingCandidates.getByTestId('workflow-dep-candidate').filter({ hasText: 'Script' }),
    ).toBeVisible();
    await expect(
      editingCandidates.getByTestId('workflow-dep-candidate').filter({ hasText: 'Storyboard' }),
    ).toBeVisible();
    await expect(
      editingCandidates.getByTestId('workflow-dep-candidate').filter({ hasText: 'Shotlist' }),
    ).toBeVisible();

    // Select Shotlist (parallel_group 1) — Storyboard shares its
    // parallel_group and must be EXCLUDED even though it's positioned
    // earlier in the draft array; Script (no parallel_group) still appears.
    await page.getByTestId('workflow-node-capsule').nth(2).click();
    const shotlistCandidates = page.getByTestId('workflow-dep-candidates');
    await expect(shotlistCandidates).toBeVisible();
    await expect(
      shotlistCandidates.getByTestId('workflow-dep-candidate').filter({ hasText: 'Script' }),
    ).toBeVisible();
    await expect(
      shotlistCandidates.getByTestId('workflow-dep-candidate').filter({ hasText: 'Storyboard' }),
    ).toHaveCount(0);

    await page.screenshot({
      path: `${SHOTS}/18-deps-editor-parallel-exclusion-${theme}.png`,
      fullPage: true,
    });
  });

  test(`${theme}: WorkflowStrip locks the current node while its dependency is unmet`, async ({ page }) => {
    await setupWorkspaceStubs(page);
    await useEnglishLocale(page);
    await forceTheme(page, theme);

    // B2 #1712 positive assertion — the workflow read must carry the resolved
    // episode id on the query (proves the `?episode_id=` path, not a null omit).
    const workflowReads: string[] = [];
    page.on('request', (req) => {
      const u = req.url();
      if (/\/api\/v1\/projects\/[^/]+\/workflow(\?|$)/.test(u)) workflowReads.push(u);
    });

    await openWorkspaceOverview(page);

    expect(workflowReads.some((u) => u.includes(`episode_id=${EPISODE_ID}`))).toBe(true);

    const storyboard = page.getByTestId('workflow-strip-node').filter({ hasText: 'Storyboard' });
    await expect(storyboard).toHaveAttribute('data-locked', 'true');
    await expect(storyboard.getByTestId('workflow-node-locked')).toBeVisible();

    // Script (the unmet dependency, still in_progress) is not itself locked —
    // only the group actually blocked from advancing gets the icon.
    const script = page.getByTestId('workflow-strip-node').filter({ hasText: 'Script' });
    await expect(script.getByTestId('workflow-node-locked')).toHaveCount(0);

    await page.screenshot({ path: `${SHOTS}/16-deps-strip-lock-${theme}.png`, fullPage: true });
  });

  test(`${theme}: Stage Board "Waiting on" row + advance dialog renders the server DEPS_PENDING ruling`, async ({ page }) => {
    await setupWorkspaceStubs(page);
    await useEnglishLocale(page);
    await forceTheme(page, theme);
    await openWorkspaceOverview(page);

    await page.locator('[data-testid="workflow-strip-node"][data-node-id="node-2"]').click();
    await expect(page.getByTestId('workspace-stage-board')).toBeVisible({ timeout: 15_000 });

    // Local derivation (nodeStatus.ts::unmetDeps) — the board's own "Waiting
    // on" row, off the shared workflow instance's node list.
    const waitingOn = page.getByTestId('stage-board-waiting-on');
    await expect(waitingOn).toBeVisible();
    await expect(waitingOn).toContainText('Script');

    // node-2 is the current node → the active-group action bar renders even
    // though it's locked (the server, not the client, is the advance gate).
    await page.getByTestId('stage-board-complete').click();

    // The dialog renders the SERVER's waiting_on list, never a re-derivation
    // — DEPS_PENDING_PREVIEW.waiting_on names "Storyboard" (deliberately
    // different from the board's local "Script" label above), so this
    // assertion only passes if the dialog is reading the server payload.
    const dialog = page.getByTestId('workflow-advance-dialog');
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText('waiting on other stages');
    await expect(dialog).toContainText('Storyboard');
    await expect(page.getByTestId('workflow-advance-confirm')).toHaveCount(0);

    await page.screenshot({ path: `${SHOTS}/17-deps-pending-dialog-${theme}.png`, fullPage: true });
  });
}
