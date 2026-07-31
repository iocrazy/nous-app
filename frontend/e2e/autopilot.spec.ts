import { test, expect, type Page, type Route } from '@playwright/test';
import { setupStubbedSession, TEAM_ID, PARENT_PROJECT_ID, USER_ID } from './helpers/stubs';

/**
 * Pre-merge visual walkthrough for Project Workflow M4 Autopilot (mig 395,
 * task O4 — the final task of the M4 workflow, see plan §O4 + the O3
 * contract it verifies).
 *
 * Full-stub harness (same contract as stage-board.spec.ts / workflow-walkthrough.spec.ts):
 * no real backend, no login. Five surfaces land in this one file since they
 * all share the same M4 Autopilot fixtures (a workflow with an in_review
 * active node + a deps-satisfied future node):
 *   1. Brief field — blur-save PATCHes `{ brief }` (task O1/O3).
 *   2. Start early — a deps-satisfied future node shows the button; a click
 *      POSTs start-early; a server DEPS_PENDING 422 surfaces the shared
 *      waiting-on toast copy (task O2/O3).
 *   3. Autopilot chip (top bar) — toggle PATCHes (actually a PUT, see
 *      projectsService.updateProject — the chip's own doc comment calls it
 *      "PATCH" loosely, matching the backend route's semantics, not the verb)
 *      `{ autopilot_enabled: false }` (task O1/O3).
 *   4. Template editor Events tab's 5th toggle ("Auto-start when ready") —
 *      Save PATCHes `events.auto_start: true` for the node (task O1/O3).
 *   5. Double-theme screenshot pair — Stage Board with the brief pinned
 *      block + the Autopilot chip on the same screen.
 *
 * Screenshots land in test-results/workflow/ for human review, continuing
 * the numbering after workflow-deps.spec.ts's 15-18.
 */

const SHOTS = 'test-results/workflow';

const PROJECT = {
  id: PARENT_PROJECT_ID,
  name: 'Autopilot E2E Project',
  description: null,
  team_id: TEAM_ID,
  project_type: 'internal',
  project_group: null,
  is_starred: false,
  is_archived: false,
  file_count: 1,
  latest_activity: null,
  current_node_id: 'node-2',
  // M4 Autopilot (mig 395): project-level master switch — starts ON so the
  // toggle test below asserts the OFF-going PATCH/PUT body.
  autopilot_enabled: true,
  workflow_badge: {
    current_node_name: 'Storyboard',
    workflow_total: 4,
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
    ...extra,
  };
}

// node-2 is the current (active) node — `in_review` so the brief field stays
// editable (brief is only read-only once done/skipped) while also being the
// status that pins it in the Stage Board's review block once non-empty.
// node-3 is a FUTURE node with no `depends_on` at all → unmetDeps() is
// trivially empty → eligible for Start early. node-4 depends on node-3
// (still pending, unsatisfied) so it must NOT also render a Start-early
// button — otherwise `workflow-start-early` would match two elements.
const WORKFLOW = {
  has_workflow: true,
  current_node_id: 'node-2',
  agents_active: 0,
  nodes: [
    node('node-1', 'Script', 0, 'done'),
    node('node-2', 'Storyboard', 1, 'in_review', {
      planned_start: '2026-07-20',
      planned_due: '2026-07-25',
      brief: '',
    }),
    node('node-3', 'Editing', 2, 'pending'),
    node('node-4', 'Distribution', 3, 'pending', { depends_on: ['node-3'] }),
  ],
};

// Variant used only by the item-5 screenshot test — node-2 already carries a
// saved brief (so the Stage Board's `stage-board-brief-pinned` block renders)
// — kept as a separate object (rather than mutating WORKFLOW's node-2 in
// place) so the brief-blur-save test above keeps its original empty-brief
// node untouched (same idiom as stage-board.spec.ts's RUN_PREPARED_WORKFLOW).
const WORKFLOW_WITH_BRIEF = {
  ...WORKFLOW,
  nodes: [
    WORKFLOW.nodes[0],
    node('node-2', 'Storyboard', 1, 'in_review', {
      planned_start: '2026-07-20',
      planned_due: '2026-07-25',
      brief: 'Focus on the mountain establishing shots; keep coverage under 90s.',
    }),
    WORKFLOW.nodes[2],
    WORKFLOW.nodes[3],
  ],
};

const STAGE_BOARD_DATA_WITH_BRIEF = {
  node: WORKFLOW_WITH_BRIEF.nodes[1],
  issue: null,
  files: [],
};

// ── Template editor fixtures (item 4 — Events tab 5th toggle) ─────────────

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
    ...extra,
  };
}

const TEMPLATE_LIST = [
  { id: 'tpl-1', team_id: TEAM_ID, name: 'Short-form', is_default: true, created_by: USER_ID, created_at: '2026-07-01T00:00:00Z', updated_at: '2026-07-01T00:00:00Z', node_count: 2 },
];

const TEMPLATE_DETAIL = {
  ...TEMPLATE_LIST[0],
  nodes: [
    tplNode('tn-1', 'Script', 0),
    tplNode('tn-2', 'Storyboard', 1),
  ],
};

function json(body: unknown) {
  return (route: Route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
}

async function setupAutopilotStubs(page: Page): Promise<void> {
  await setupStubbedSession(page);
  // Registered after the harness catch-alls → these win (same convention as
  // stage-board.spec.ts / workflow-walkthrough.spec.ts).
  await page.route('**/api/v1/ai-library/agents', json([]));

  // Project list (workspace resolves selectedProject by matching the URL id).
  await page.route('**/api/v1/projects?*', json({ success: true, data: [PROJECT] }));
  // Per-project workflow instance (response_model → no envelope).
  await page.route('**/api/v1/projects/*/workflow', json(WORKFLOW));
}

async function setupTemplateStubs(page: Page): Promise<void> {
  await setupStubbedSession(page);
  await page.route('**/api/v1/ai-library/agents', json([]));
  await page.route('**/api/v1/workflows?*', json({ success: true, data: TEMPLATE_LIST }));
  await page.route('**/api/v1/workflows/*', json({ success: true, data: TEMPLATE_DETAIL }));
  await page.route('**/api/v1/workflows/stage-library', json({ success: true, data: [] }));
}

/**
 * Capture the body of the template Save PATCH (`PATCH /api/v1/workflows/{id}`)
 * — same idiom as workflow-walkthrough.spec.ts's own `capturePatch`. Registered
 * AFTER setupTemplateStubs → wins the last-registered-routes-first convention
 * for PATCH only; every other method (the plain GET list/detail fetches)
 * falls back to the earlier catch-all so existing behavior is untouched.
 */
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
  // i18n.ts defaults to 'zh' unless overridden — pin English so the copy
  // assertions below (waiting-on toast / Events tab labels) are stable.
  await page.addInitScript(() => {
    try {
      localStorage.setItem('language', 'en');
    } catch {
      /* ignore */
    }
  });
}

async function openWorkspaceOverview(page: Page): Promise<void> {
  await page.goto(`/team/${TEAM_ID}/projects/${PARENT_PROJECT_ID}`);
  await expect(page.getByTestId('workflow-strip')).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId('workflow-current-node-card').first()).toBeVisible();
}

async function openWorkspace(page: Page): Promise<void> {
  await page.goto(`/team/${TEAM_ID}/projects/${PARENT_PROJECT_ID}`);
  await expect(page.getByTestId('workspace-sidebar')).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId('ws-stage-node-2')).toBeVisible();
}

async function openTemplateEditor(page: Page): Promise<void> {
  await page.goto(`/team/${TEAM_ID}/projects`);
  await page.getByTestId('workflow-templates-entry').click();
  await expect(page.getByTestId('workflow-template-editor')).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId('workflow-node-capsule').first()).toBeVisible();
}

/** Scope the brief textarea to a specific node's card — CurrentNodeCard
 * renders `workflow-node-brief` unconditionally (active AND future peek
 * cards alike), so an unscoped `getByTestId` would match more than one
 * element whenever a future-eligible card is also on the page (node-3 here). */
function cardBrief(page: Page, nodeId: string) {
  return page.locator(`[data-testid="workflow-current-node-card"][data-node-id="${nodeId}"]`).getByTestId('workflow-node-brief');
}

test('brief field blur-save PATCHes { brief } (task O4 §1)', async ({ page }) => {
  await setupAutopilotStubs(page);
  await useEnglishLocale(page);
  await forceTheme(page, 'dark');

  let patchBody: Record<string, unknown> | null = null;
  await page.route('**/api/v1/projects/*/workflow/nodes/*', async (route) => {
    const req = route.request();
    if (req.method() !== 'PATCH') {
      await route.fallback();
      return;
    }
    patchBody = req.postDataJSON();
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: { ...WORKFLOW.nodes[1], brief: (patchBody?.brief as string) ?? '' },
      }),
    });
  });

  await openWorkspaceOverview(page);
  const briefField = cardBrief(page, 'node-2');
  await expect(briefField).toBeVisible();
  await briefField.fill('Focus on the mountain establishing shots.');
  await briefField.blur();

  await expect.poll(() => patchBody).toEqual({ brief: 'Focus on the mountain establishing shots.' });
});

test('Start early — deps-satisfied future node shows the button; click POSTs start-early (task O4 §2)', async ({ page }) => {
  await setupAutopilotStubs(page);
  await useEnglishLocale(page);
  await forceTheme(page, 'dark');

  const calls: string[] = [];
  await page.route('**/api/v1/projects/*/workflow/nodes/*/start-early', async (route) => {
    calls.push(route.request().url());
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: { ...WORKFLOW.nodes[2], status: 'in_progress' } }),
    });
  });

  await openWorkspaceOverview(page);
  // Only node-3 clears unmetDeps() (empty depends_on) — node-4 depends on it
  // and is still pending, so exactly one Start-early button renders.
  const startEarlyBtn = page.getByTestId('workflow-start-early');
  await expect(startEarlyBtn).toBeVisible();
  await startEarlyBtn.click();

  await expect.poll(() => calls.length).toBe(1);
  expect(calls[0]).toContain('/workflow/nodes/node-3/start-early');
});

test('Start early — server DEPS_PENDING 422 surfaces the shared waiting-on toast (task O4 §2)', async ({ page }) => {
  await setupAutopilotStubs(page);
  await useEnglishLocale(page);
  await forceTheme(page, 'dark');

  // The client-side gate (unmetDeps) already cleared node-3 for display, but
  // the server re-checks the same predicate on the actual call and here rules
  // it still blocked — the structured 422 `detail` (task O3's apiClient.ts
  // fix unwraps this into ApiError.code/.details) maps to the same
  // `deps.waitingOn` i18n copy the Stage Board's own "Waiting on" row uses.
  await page.route('**/api/v1/projects/*/workflow/nodes/*/start-early', (route) =>
    route.fulfill({
      status: 422,
      contentType: 'application/json',
      body: JSON.stringify({ detail: { code: 'DEPS_PENDING', waiting_on: ['Script'] } }),
    }),
  );

  await openWorkspaceOverview(page);
  await page.getByTestId('workflow-start-early').click();

  await expect(page.getByText('Waiting on: Script')).toBeVisible();
});

test('Autopilot chip toggle PATCHes { autopilot_enabled: false } (task O4 §3)', async ({ page }) => {
  await setupAutopilotStubs(page);
  await useEnglishLocale(page);
  await forceTheme(page, 'dark');

  // updateProject actually fires a PUT (apiClient.put), not a PATCH — the
  // chip's own doc comment in WorkspaceTopBar.tsx calls it "PATCH" loosely
  // (matching the backend route's semantics), so assert the real wire verb.
  let putBody: Record<string, unknown> | null = null;
  await page.route('**/api/v1/projects/*', async (route) => {
    const req = route.request();
    if (req.method() !== 'PUT') {
      await route.fallback();
      return;
    }
    putBody = req.postDataJSON();
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: { ...PROJECT, autopilot_enabled: false } }),
    });
  });

  await openWorkspaceOverview(page);
  const chip = page.getByTestId('workspace-autopilot-chip');
  await expect(chip).toHaveAttribute('data-autopilot', 'on');
  await chip.click();

  await expect.poll(() => putBody).toEqual({ autopilot_enabled: false });
  await expect(chip).toHaveAttribute('data-autopilot', 'off');
});

test('Template editor Events tab Auto-start toggle, Save PATCHes events.auto_start=true (task O4 §4)', async ({ page }) => {
  await setupTemplateStubs(page);
  const patch = capturePatch(page);
  await useEnglishLocale(page);
  await forceTheme(page, 'dark');
  await openTemplateEditor(page);

  // First node (Script) is auto-selected on load.
  await expect(page.getByTestId('workflow-node-capsule').first()).toContainText('Script');

  await page.getByRole('button', { name: 'Events', exact: true }).click();
  const autoStart = page.getByRole('switch', { name: 'Auto-start when ready' });
  await expect(autoStart).toHaveAttribute('aria-checked', 'false');
  await autoStart.click();
  await expect(autoStart).toHaveAttribute('aria-checked', 'true');

  await page.getByTestId('workflow-save-template').click();
  await expect.poll(() => patch.get()).not.toBeNull();
  const body = patch.get() as { nodes: Array<Record<string, unknown>> };
  const scriptNode = body.nodes.find((n) => n.name === 'Script') as
    | { events: Record<string, boolean> }
    | undefined;
  expect(scriptNode).toBeDefined();
  expect(scriptNode!.events).toMatchObject({ auto_start: true });
});

for (const theme of ['dark', 'light'] as const) {
  test(`${theme}: Stage Board brief pinned + Autopilot chip render together (task O4 §5)`, async ({ page }) => {
    await setupAutopilotStubs(page);
    await useEnglishLocale(page);
    await forceTheme(page, theme);

    // Override the generic workflow/board routes with the brief-bearing
    // node-2 — registered after setupAutopilotStubs's routes (last-registered-
    // wins convention this file family already uses).
    await page.route('**/api/v1/projects/*/workflow', json(WORKFLOW_WITH_BRIEF));
    await page.route(
      '**/api/v1/projects/*/workflow/nodes/*/board',
      json({ success: true, data: STAGE_BOARD_DATA_WITH_BRIEF }),
    );

    await openWorkspace(page);
    await page.getByTestId('ws-stage-node-2').click();
    await expect(page.getByTestId('workspace-stage-board')).toBeVisible({ timeout: 15_000 });

    // The in_review node's saved brief pins to the top of the board...
    const pinned = page.getByTestId('stage-board-brief-pinned');
    await expect(pinned).toBeVisible();
    await expect(pinned).toContainText('mountain establishing shots');
    // ...on the same screen as the Autopilot chip (top bar, always rendered
    // regardless of which workspace module is open).
    await expect(page.getByTestId('workspace-autopilot-chip')).toHaveAttribute('data-autopilot', 'on');

    await page.screenshot({ path: `${SHOTS}/19-autopilot-brief-chip-${theme}.png`, fullPage: true });
  });
}
