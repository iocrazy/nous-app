import { test, expect, type Page, type Route } from '@playwright/test';
import { setupStubbedSession, TEAM_ID, PARENT_PROJECT_ID, USER_ID } from './helpers/stubs';

/**
 * Pre-merge visual walkthrough for Project Workflow M2 PR-F F4 (Stage Board).
 *
 * Full-stub harness (same contract as workflow-walkthrough.spec.ts): no real
 * backend, no login. Covers the sidebar Stages → Stage Board module hop that
 * F2/F3 landed: click a Stages node in the sidebar → the URL picks up
 * `?module=stage&node={id}` → the board's three sections (header / Tasks /
 * Deliverables) render off one `fetchStageBoard` call → the active node's
 * "Complete Stage" button opens the shared AdvanceConfirmDialog (stubbed
 * advance-preview; the dialog is asserted open, never confirmed).
 *
 * Screenshots land in test-results/workflow/ for human review, alongside the
 * PR-C walkthrough's shots.
 */

const SHOTS = 'test-results/workflow';

const AGENT_ID = '00000000-0000-4000-8000-0000000000a1';

const PROJECT = {
  id: PARENT_PROJECT_ID,
  name: 'Stage Board E2E Project',
  description: null,
  team_id: TEAM_ID,
  project_type: 'internal',
  project_group: null,
  is_starred: false,
  is_archived: false,
  file_count: 1,
  latest_activity: null,
  current_node_id: 'node-2',
  workflow_badge: {
    current_node_name: 'Storyboard',
    workflow_total: 4,
    workflow_position: 2,
    agents_active: 1,
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

// The workflow instance — node-2 (Storyboard) is `current_node_id`, so it's
// the one node in the active group and the only one that renders a Stage
// Board "Complete Stage" action bar (#1400).
const WORKFLOW = {
  has_workflow: true,
  current_node_id: 'node-2',
  agents_active: 1,
  nodes: [
    node('node-1', 'Script', 0, 'done'),
    node('node-2', 'Storyboard', 1, 'in_progress', {
      owner_agent_id: AGENT_ID,
      planned_start: '2026-07-20',
      planned_due: '2026-07-25',
      review_required: true,
      deliverable_required: true,
      deliverable_label: 'Shot list',
      folder_id: null,
    }),
    node('node-3', 'Editing', 2, 'pending'),
    node('node-4', 'Distribution', 3, 'pending'),
  ],
};

// `GET .../workflow/nodes/{id}/board` payload for node-2 — a mirror issue
// with one sub-issue, plus one filed deliverable (no folder_id on the node →
// the read-only file list branch, not the live DeliverablesZone dropzone).
const STAGE_BOARD_DATA = {
  node: WORKFLOW.nodes[1],
  issue: {
    id: '100',
    identifier: 'ENG-42',
    title: 'Storyboard mirror issue',
    status: 'in_progress',
    assignee: { user_id: null, agent_id: AGENT_ID },
    sub_issues: [
      {
        id: '101',
        identifier: 'ENG-43',
        title: 'Shot 1',
        status: 'todo',
        assignee: { user_id: null, agent_id: null },
      },
    ],
  },
  files: [
    { id: 'f1', filename: 'shotlist_v1.pdf', size: 2048, created_at: '2026-07-22T00:00:00Z', source_issue_identifier: 'ENG-42' },
  ],
};

const ADVANCE_PREVIEW = {
  direction: 'forward',
  will_advance: true,
  blocked_reason: null,
  closing: [{ node_id: 'node-2', name: 'Storyboard', assignee_user_id: null, assignee_agent_id: AGENT_ID, due_date: '2026-07-25' }],
  creating: [{ node_id: 'node-3', name: 'Editing', assignee_user_id: null, assignee_agent_id: null, due_date: '2026-08-01' }],
  warnings: [],
};

// Run now (H3/H4) fixtures — node-2 with the stage hook already fired
// (`metadata.run_prepared_at` set) and `suggest_agent_run` enabled, so the
// header renders the solid "Run now" button instead of the plain suggest
// chip. Kept separate from `WORKFLOW`/`STAGE_BOARD_DATA` above (rather than
// mutating node-2 in place) so the Complete Stage tests keep their original
// `suggest_agent_run: false` node untouched.
const RUN_PREPARED_WORKFLOW = {
  ...WORKFLOW,
  nodes: [
    WORKFLOW.nodes[0],
    node('node-2', 'Storyboard', 1, 'in_progress', {
      owner_agent_id: AGENT_ID,
      planned_start: '2026-07-20',
      planned_due: '2026-07-25',
      review_required: true,
      deliverable_required: true,
      deliverable_label: 'Shot list',
      folder_id: null,
      events: { notify_on_arrival: true, notify_on_complete: false, suggest_agent_run: true },
      metadata: { run_prepared_at: '2026-07-24T00:00:00Z' },
    }),
    WORKFLOW.nodes[2],
    WORKFLOW.nodes[3],
  ],
};

const RUN_PREPARED_STAGE_BOARD_DATA = {
  ...STAGE_BOARD_DATA,
  node: RUN_PREPARED_WORKFLOW.nodes[1],
};

const DISPATCH_PREVIEW = {
  will_start: true,
  agent_id: AGENT_ID,
  blocked_reason: null,
};

// Deliverable-form fixtures (mig 390, M3 PR-I §2, task I4) — node-2 with a
// two-field form_schema (one required, unfilled), kept separate from
// STAGE_BOARD_DATA/WORKFLOW above (rather than mutating node-2 in place) so
// the Complete Stage / Run now tests keep their original schema-less node
// untouched (spec §2: schema empty → StageNodeForm renders nothing at all).
const FORM_SCHEMA = [
  { key: 'summary', label: 'Summary', type: 'text', required: true },
  { key: 'notes', label: 'Notes', type: 'textarea', required: false },
];

const FORM_STAGE_BOARD_DATA = {
  ...STAGE_BOARD_DATA,
  node: { ...STAGE_BOARD_DATA.node, form_schema: FORM_SCHEMA, form_data: {} },
};

function json(body: unknown) {
  return (route: Route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
}

async function setupStageBoardStubs(page: Page): Promise<void> {
  await setupStubbedSession(page);
  // Registered after the harness catch-alls → these win (same convention as
  // workflow-walkthrough.spec.ts).
  await page.route('**/api/v1/ai-library/agents', json([]));

  // Project list (workspace resolves selectedProject by matching the URL id).
  await page.route('**/api/v1/projects?*', json({ success: true, data: [PROJECT] }));
  // Per-project workflow instance (response_model → no envelope) + advance
  // preview for the Complete Stage click.
  await page.route('**/api/v1/projects/*/workflow', json(WORKFLOW));
  await page.route('**/api/v1/projects/*/advance-preview*', json(ADVANCE_PREVIEW));
  // Stage Board aggregate (F1) — `data`-wrapped envelope (fetchStageBoard
  // reads `response.data`, not the bare model). Distinct path from the bare
  // `/workflow` route above (glob `*` doesn't cross `/`), so registration
  // order doesn't matter here, but keep the same "specific routes after the
  // catch-all" convention regardless.
  await page.route('**/api/v1/projects/*/workflow/nodes/*/board', json({ success: true, data: STAGE_BOARD_DATA }));
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
  // assertions below (Complete Stage / No mirror issue yet) are stable.
  await page.addInitScript(() => {
    try {
      localStorage.setItem('language', 'en');
    } catch {
      /* ignore */
    }
  });
}

async function openWorkspace(page: Page): Promise<void> {
  await page.goto(`/team/${TEAM_ID}/projects/${PARENT_PROJECT_ID}`);
  await expect(page.getByTestId('workspace-sidebar')).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId('ws-stage-node-2')).toBeVisible();
}

for (const theme of ['dark', 'light'] as const) {
  test(`${theme}: Stages sidebar click opens the Stage Board module`, async ({ page }) => {
    await setupStageBoardStubs(page);
    await useEnglishLocale(page);
    await forceTheme(page, theme);
    await openWorkspace(page);

    // Sidebar Stages click → stage module opens with the node id in the URL.
    await page.getByTestId('ws-stage-node-2').click();
    await expect(page).toHaveURL(/[?&]module=stage(&|$)/);
    await expect(page).toHaveURL(/[?&]node=node-2(&|$)/);

    // Three sections all render off the one fetchStageBoard call.
    await expect(page.getByTestId('workspace-stage-board')).toBeVisible({ timeout: 15_000 });
    const header = page.getByTestId('stage-board-header');
    const tasks = page.getByTestId('stage-board-tasks');
    const deliverables = page.getByTestId('stage-board-deliverables');
    await expect(header).toBeVisible();
    await expect(tasks).toBeVisible();
    await expect(deliverables).toBeVisible();

    await expect(header).toContainText('Storyboard');
    await expect(tasks).toContainText('ENG-42');
    await expect(tasks).toContainText('Shot 1');
    await expect(deliverables).toContainText('shotlist_v1.pdf');

    await page.screenshot({ path: `${SHOTS}/10-stage-board-${theme}.png`, fullPage: true });
  });

  test(`${theme}: Stage Board Complete Stage opens the advance confirm dialog`, async ({ page }) => {
    await setupStageBoardStubs(page);
    await useEnglishLocale(page);
    await forceTheme(page, theme);
    await openWorkspace(page);

    await page.getByTestId('ws-stage-node-2').click();
    await expect(page.getByTestId('workspace-stage-board')).toBeVisible({ timeout: 15_000 });

    // node-2 is the current node → the active-group action bar renders.
    const completeButton = page.getByTestId('stage-board-complete');
    await expect(completeButton).toBeVisible();
    await completeButton.click();

    // Dialog renders the server-ruled preview; assert it opened, don't confirm.
    await expect(page.getByTestId('workflow-advance-dialog')).toBeVisible();
    await expect(page.getByTestId('workflow-advance-dialog')).toContainText('Storyboard');
    await expect(page.getByTestId('workflow-advance-dialog')).toContainText('Editing');

    await page.screenshot({ path: `${SHOTS}/11-stage-board-advance-${theme}.png`, fullPage: true });
  });

  test(`${theme}: Stage Board Run now opens the dispatch confirm dialog, never dispatches`, async ({ page }) => {
    await setupStageBoardStubs(page);
    await useEnglishLocale(page);
    await forceTheme(page, theme);

    // Override the generic workflow/board routes with the run-prepared
    // node-2 payload — registered after setupStageBoardStubs's routes, so
    // (same last-registered-wins convention as the rest of this file) these
    // win for this test only.
    await page.route('**/api/v1/projects/*/workflow', json(RUN_PREPARED_WORKFLOW));
    await page.route(
      '**/api/v1/projects/*/workflow/nodes/*/board',
      json({ success: true, data: RUN_PREPARED_STAGE_BOARD_DATA }),
    );
    await page.route('**/api/v1/issues/*/dispatch-preview', json(DISPATCH_PREVIEW));

    // Registered BEFORE any interaction below — fails the assertion at the
    // end if the real dispatch POST ever fires. This test only opens the
    // confirm dialog; it must never click "Start working".
    let dispatchCalls = 0;
    await page.route('**/api/v1/issues/*/dispatch', (route) => {
      dispatchCalls += 1;
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
    });

    await openWorkspace(page);
    await page.getByTestId('ws-stage-node-2').click();
    await expect(page.getByTestId('workspace-stage-board')).toBeVisible({ timeout: 15_000 });

    // The stage hook already prepared a run → solid "Run now" button, not
    // the plain suggest chip.
    const runNowButton = page.getByTestId('stage-board-run-now');
    await expect(runNowButton).toBeVisible();
    await expect(page.getByTestId('stage-board-suggest-chip')).toHaveCount(0);
    await runNowButton.click();

    // DispatchConfirmDialog renders the server-ruled preview; assert it
    // opened, never confirm it.
    const dialog = page.getByRole('dialog', { name: 'Confirm dispatch' });
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText('will start working on this issue');

    await page.screenshot({ path: `${SHOTS}/12-stage-board-run-now-${theme}.png`, fullPage: true });

    expect(dispatchCalls).toBe(0);
  });

  test(`${theme}: Stage Board form — filling a field PATCHes only that key (task I4)`, async ({ page }) => {
    await setupStageBoardStubs(page);
    await useEnglishLocale(page);
    await forceTheme(page, theme);

    // Override the board fetch with the form-bearing node — registered after
    // setupStageBoardStubs's routes (last-registered-wins convention).
    await page.route(
      '**/api/v1/projects/*/workflow/nodes/*/board',
      json({ success: true, data: FORM_STAGE_BOARD_DATA }),
    );

    // Capture the node PATCH — distinct path from `.../board` above (glob
    // `*` doesn't cross `/`), so registration order between the two doesn't
    // matter; every non-PATCH method falls back untouched.
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
          data: { ...FORM_STAGE_BOARD_DATA.node, form_data: patchBody?.form_data ?? {} },
        }),
      });
    });

    await openWorkspace(page);
    await page.getByTestId('ws-stage-node-2').click();
    await expect(page.getByTestId('workspace-stage-board')).toBeVisible({ timeout: 15_000 });

    const form = page.getByTestId('stage-node-form');
    await expect(form).toBeVisible();
    // Required, unfilled field — badge marks it before any input.
    await expect(page.getByTestId('stage-form-required-summary')).toHaveAttribute('data-filled', 'false');

    const summaryField = page.getByTestId('stage-form-field-summary');
    await summaryField.fill('Q3 recap');
    await summaryField.blur();

    await expect.poll(() => patchBody).toEqual({ form_data: { summary: 'Q3 recap' } });
    // Badge flips once the server echoes the saved value back into the node.
    await expect(page.getByTestId('stage-form-required-summary')).toHaveAttribute('data-filled', 'true');

    await page.screenshot({ path: `${SHOTS}/13-stage-board-form-${theme}.png`, fullPage: true });
  });

  test(`${theme}: Stage Board Complete Stage surfaces FORM_INCOMPLETE with the missing field labels (task I4)`, async ({ page }) => {
    await setupStageBoardStubs(page);
    await useEnglishLocale(page);
    await forceTheme(page, theme);

    await page.route(
      '**/api/v1/projects/*/advance-preview*',
      json({
        direction: 'forward',
        will_advance: false,
        blocked_reason: 'FORM_INCOMPLETE',
        closing: [],
        creating: [],
        warnings: [],
        missing_fields: ['Summary'],
      }),
    );

    await openWorkspace(page);
    await page.getByTestId('ws-stage-node-2').click();
    await expect(page.getByTestId('workspace-stage-board')).toBeVisible({ timeout: 15_000 });

    const completeButton = page.getByTestId('stage-board-complete');
    await expect(completeButton).toBeVisible();
    await completeButton.click();

    const dialog = page.getByTestId('workflow-advance-dialog');
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("Some required form fields aren't filled in yet.");
    await expect(dialog).toContainText('Summary');
    // Blocked ruling never renders a confirm button (#1400: no path forward
    // until the server's own predicate clears).
    await expect(page.getByTestId('workflow-advance-confirm')).toHaveCount(0);

    await page.screenshot({ path: `${SHOTS}/14-stage-board-form-incomplete-${theme}.png`, fullPage: true });
  });
}
