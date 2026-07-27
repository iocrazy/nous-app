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
}
