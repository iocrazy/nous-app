import { test, expect, type Page, type Route } from '@playwright/test';
import { setupStubbedSession, TEAM_ID, PARENT_PROJECT_ID, USER_ID } from './helpers/stubs';

/**
 * Pre-merge visual walkthrough for Project Workflow M1 PR-C (template editor +
 * workspace strip / node card + advance gate).
 *
 * Full-stub harness (same contract as issue-trigger-walkthrough / the storyboard
 * suite): no real backend, no login. The strip, node card and advance dialog
 * render the server payloads verbatim, so the stubbed workflow endpoints stand
 * in for the PR-A/PR-B backend tests that already pin template CRUD, the
 * instance shape and the advance predicate.
 *
 * Screenshots land in test-results/workflow/ for human review.
 */

const SHOTS = 'test-results/workflow';

const AGENT = {
  id: '00000000-0000-4000-8000-0000000000a1',
  slug: 'script-ai',
  name: 'Script AI',
  description: null,
  icon: 'bot',
  model: 'qwen-max',
  temperature: 0.7,
};

const STAGE_LIBRARY = [
  { id: '900001', slug: 'script', name: 'Script', sort_order: 0, phase: 'pre', default_role_label: 'Writer', deliverable_label: 'Final script', review_required: true },
  { id: '900002', slug: 'storyboard', name: 'Storyboard', sort_order: 1, phase: 'pre', default_role_label: 'Artist', deliverable_label: 'Shot list', review_required: true },
  { id: '900003', slug: 'voiceover', name: 'Voiceover', sort_order: 2, phase: 'pre', default_role_label: 'VO', deliverable_label: 'VO track', review_required: false },
  { id: '900004', slug: 'canvas', name: 'Canvas', sort_order: 3, phase: 'production', default_role_label: 'Gen AI', deliverable_label: 'Generated clips', review_required: true },
  { id: '900005', slug: 'editing', name: 'Editing', sort_order: 4, phase: 'post', default_role_label: 'Editor', deliverable_label: 'A/B copy', review_required: true },
  { id: '900006', slug: 'distribution', name: 'Distribution', sort_order: 5, phase: 'wrap', default_role_label: 'Ops', deliverable_label: 'Published links', review_required: false },
];

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
    // D4 (mig 386): completion_policy + events are optional server-side, but a
    // post-386 payload always carries them — stub the realistic shape rather
    // than relying on the frontend's normalizeTemplateNode() fallback.
    completion_policy: 'owner',
    events: { notify_on_arrival: true, notify_on_complete: false, suggest_agent_run: false },
    ...extra,
  };
}

const TEMPLATE_LIST = [
  { id: 'tpl-1', team_id: TEAM_ID, name: 'Short-form', is_default: true, created_by: USER_ID, created_at: '2026-07-01T00:00:00Z', updated_at: '2026-07-01T00:00:00Z', node_count: 4 },
  { id: 'tpl-2', team_id: TEAM_ID, name: 'Long-form', is_default: false, created_by: USER_ID, created_at: '2026-07-01T00:00:00Z', updated_at: '2026-07-01T00:00:00Z', node_count: 4 },
];

const TEMPLATE_DETAIL = {
  ...TEMPLATE_LIST[0],
  nodes: [
    tplNode('tn-1', 'Script', 0, { review_required: true, deliverable_label: 'Final script', default_owner_agent_id: AGENT.id }),
    tplNode('tn-2', 'Storyboard', 1, { review_required: true, deliverable_required: true, deliverable_label: 'Shot list' }),
    tplNode('tn-3', 'Editing', 2, { parallel_group: 1 }),
    tplNode('tn-4', 'Color Grading', 3, { parallel_group: 1 }),
    tplNode('tn-5', 'Distribution', 4),
  ],
};

const PROJECT = {
  id: PARENT_PROJECT_ID,
  name: 'Workflow E2E Project',
  description: null,
  team_id: TEAM_ID,
  project_type: 'internal',
  project_group: null,
  is_starred: false,
  is_archived: false,
  file_count: 3,
  latest_activity: null,
  current_node_id: 'node-2',
  // W3-3: batch-derived workflow badge for the list card/row.
  workflow_badge: {
    current_node_name: 'Storyboard',
    workflow_total: 4,
    workflow_position: 2,
    agents_active: 2,
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
    ...extra,
  };
}

const WORKFLOW = {
  has_workflow: true,
  current_node_id: 'node-2',
  agents_active: 1,
  nodes: [
    node('node-1', 'Script', 0, 'done'),
    node('node-2', 'Storyboard', 1, 'in_progress', {
      owner_agent_id: AGENT.id,
      planned_start: '2026-07-20',
      planned_due: '2026-07-25',
      review_required: true,
      deliverable_required: true,
      deliverable_label: 'Shot list',
      folder_id: 'folder-2',
      deliverable_file_count: 2,
    }),
    // W3-2: a pending node past its due date → rose capsule + "Overdue" tag.
    node('node-3', 'Editing', 2, 'pending', { planned_due: '2020-01-01' }),
    node('node-4', 'Distribution', 3, 'pending'),
  ],
};

const ADVANCE_PREVIEW = {
  direction: 'forward',
  will_advance: true,
  blocked_reason: null,
  closing: [{ node_id: 'node-2', name: 'Storyboard', assignee_user_id: null, assignee_agent_id: AGENT.id, due_date: '2026-07-25' }],
  creating: [{ node_id: 'node-3', name: 'Editing', assignee_user_id: null, assignee_agent_id: null, due_date: '2026-08-01' }],
  warnings: [],
};

function json(body: unknown) {
  return (route: Route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
}

async function setupWorkflowStubs(page: Page): Promise<void> {
  await setupStubbedSession(page);
  // Registered after the harness catch-alls → these win.
  await page.route('**/api/v1/ai-library/agents', json([AGENT]));

  // Team templates (list + detail + node bank).
  await page.route('**/api/v1/workflows?*', json({ success: true, data: TEMPLATE_LIST }));
  await page.route('**/api/v1/workflows/*', json({ success: true, data: TEMPLATE_DETAIL }));
  // stage-library registered LAST so it wins over the `/workflows/*` glob.
  await page.route('**/api/v1/workflows/stage-library', json({ success: true, data: STAGE_LIBRARY }));

  // Project list (workspace resolves selectedProject by matching the URL id).
  await page.route('**/api/v1/projects?*', json({ success: true, data: [PROJECT] }));
  // Per-project workflow instance + advance preview (response_model → no envelope).
  await page.route('**/api/v1/projects/*/workflow', json(WORKFLOW));
  await page.route('**/api/v1/projects/*/advance-preview*', json(ADVANCE_PREVIEW));
  // Node collection (W3-1): POST add / DELETE remove. Distinct paths from the
  // bare `/workflow` route above; registered last per the last-wins convention.
  await page.route('**/api/v1/projects/*/workflow/nodes', json({ success: true, data: node('node-new', 'Voiceover', 4, 'pending') }));
  await page.route('**/api/v1/projects/*/workflow/nodes/*', json({ success: true, data: { deleted: true } }));
}

/**
 * D4: capture the body of the template Save PATCH (`PATCH /api/v1/workflows/{id}`)
 * so a test can assert the edited completion_policy/events actually rode the
 * wire, not just that the UI toggled visually.
 *
 * Registered AFTER setupWorkflowStubs → wins the last-registered-routes-first
 * convention this file already uses (see the stage-library / node-collection
 * comments above) for PATCH only; every other method (the plain GET list/detail
 * fetches) falls back to the earlier catch-all so existing behavior is untouched.
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

async function openTemplateEditor(page: Page): Promise<void> {
  await page.goto(`/team/${TEAM_ID}/projects`);
  await page.getByTestId('workflow-templates-entry').click();
  await expect(page.getByTestId('workflow-template-editor')).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId('workflow-node-capsule').first()).toBeVisible();
}

async function openWorkspaceOverview(page: Page): Promise<void> {
  await page.goto(`/team/${TEAM_ID}/projects/${PARENT_PROJECT_ID}`);
  await expect(page.getByTestId('workflow-strip')).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId('workflow-current-node-card').first()).toBeVisible();
}

for (const theme of ['dark', 'light'] as const) {
  test(`${theme}: template editor renders chain + inspector`, async ({ page }) => {
    await setupWorkflowStubs(page);
    await forceTheme(page, theme);
    await openTemplateEditor(page);
    await page.screenshot({ path: `${SHOTS}/01-template-editor-${theme}.png`, fullPage: true });
  });

  test(`${theme}: template editor edits Flow Rules & Events, Save PATCHes them (D4)`, async ({ page }) => {
    await setupWorkflowStubs(page);
    const patch = capturePatch(page);
    await forceTheme(page, theme);
    // This test asserts on i18n copy (tab labels / radio & toggle names), so
    // pin the locale to English — i18n.ts defaults to 'zh' unless overridden
    // (same idiom as e.g. e2e/projects-workspace.spec.ts).
    await page.addInitScript(() => {
      try {
        localStorage.setItem('language', 'en');
      } catch {
        /* ignore */
      }
    });
    await openTemplateEditor(page);

    // First node (Script) is auto-selected on load — its stubbed defaults are
    // completion_policy: 'owner' and events.notify_on_complete: false.
    await expect(page.getByTestId('workflow-node-capsule').first()).toContainText('Script');

    // Flow Rules tab: switch policy to "any editor".
    await page.getByRole('button', { name: 'Flow Rules', exact: true }).click();
    const ownerRadio = page.getByRole('radio', { name: 'Owner reviews & completes (default)' });
    const anyEditorRadio = page.getByRole('radio', { name: 'Any editor can complete' });
    await expect(ownerRadio).toBeChecked();
    await anyEditorRadio.check();
    await expect(anyEditorRadio).toBeChecked();
    await page.screenshot({ path: `${SHOTS}/08-flow-rules-${theme}.png`, fullPage: true });

    // Events tab: flip "Notify on completion" on.
    await page.getByRole('button', { name: 'Events', exact: true }).click();
    const notifyOnComplete = page.getByRole('switch', { name: 'Notify on completion' });
    await expect(notifyOnComplete).toHaveAttribute('aria-checked', 'false');
    await notifyOnComplete.click();
    await expect(notifyOnComplete).toHaveAttribute('aria-checked', 'true');
    await page.screenshot({ path: `${SHOTS}/09-events-${theme}.png`, fullPage: true });

    // Save → PATCH payload must carry both changes for the Script node.
    await page.getByTestId('workflow-save-template').click();
    await expect.poll(() => patch.get()).not.toBeNull();
    const body = patch.get() as { nodes: Array<Record<string, unknown>> };
    const scriptNode = body.nodes.find((n) => n.name === 'Script') as
      | { completion_policy: string; events: Record<string, boolean> }
      | undefined;
    expect(scriptNode).toBeDefined();
    expect(scriptNode!.completion_policy).toBe('any_editor');
    expect(scriptNode!.events).toMatchObject({
      notify_on_arrival: true,
      notify_on_complete: true,
      suggest_agent_run: false,
    });
  });

  test(`${theme}: workspace strip + current node card`, async ({ page }) => {
    await setupWorkflowStubs(page);
    await forceTheme(page, theme);
    await openWorkspaceOverview(page);
    // The deliverable row surfaces the server-computed filed-file count.
    await expect(page.getByTestId('workflow-node-filed-count').first()).toContainText('2');
    await page.screenshot({ path: `${SHOTS}/02-overview-${theme}.png`, fullPage: true });
  });

  test(`${theme}: advance confirm dialog renders the server preview`, async ({ page }) => {
    await setupWorkflowStubs(page);
    await forceTheme(page, theme);
    await openWorkspaceOverview(page);
    await page.getByTestId('workflow-complete-stage').click();
    await expect(page.getByTestId('workflow-advance-dialog')).toBeVisible();
    // The dialog renders the server ruling: closing Storyboard, creating Editing.
    await expect(page.getByTestId('workflow-advance-dialog')).toContainText('Storyboard');
    await expect(page.getByTestId('workflow-advance-dialog')).toContainText('Editing');
    await page.screenshot({ path: `${SHOTS}/03-advance-dialog-${theme}.png`, fullPage: true });
  });

  test(`${theme}: overdue node shows a rose Overdue tag (W3-2)`, async ({ page }) => {
    await setupWorkflowStubs(page);
    await forceTheme(page, theme);
    await openWorkspaceOverview(page);
    // The pending Editing node is past its due date → rose capsule + tag.
    await expect(page.getByTestId('workflow-overdue-tag').first()).toBeVisible();
    const editing = page.getByTestId('workflow-strip-node').filter({ hasText: 'Editing' });
    await expect(editing).toHaveAttribute('data-overdue', 'true');
    await page.screenshot({ path: `${SHOTS}/04-overdue-${theme}.png`, fullPage: true });
  });

  test(`${theme}: add stage opens the library picker (W3-1)`, async ({ page }) => {
    await setupWorkflowStubs(page);
    await forceTheme(page, theme);
    await openWorkspaceOverview(page);
    await page.getByTestId('workflow-add-stage').click();
    await expect(page.getByTestId('workflow-library-picker')).toBeVisible();
    // The bank list + the blank-stage footer are both offered on the instance path.
    await expect(page.getByTestId('workflow-library-item').first()).toBeVisible();
    await expect(page.getByTestId('workflow-blank-stage-input')).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/05-add-library-${theme}.png`, fullPage: true });
  });

  test(`${theme}: remove pending node opens a confirm dialog (W3-1)`, async ({ page }) => {
    await setupWorkflowStubs(page);
    await forceTheme(page, theme);
    await openWorkspaceOverview(page);
    const editing = page.getByTestId('workflow-strip-node').filter({ hasText: 'Editing' });
    await editing.hover();
    await page.getByTestId('workflow-remove-node').first().click();
    await expect(page.getByTestId('workflow-remove-confirm')).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/06-remove-confirm-${theme}.png`, fullPage: true });
  });

  test(`${theme}: projects list shows the workflow badge (W3-3)`, async ({ page }) => {
    await setupWorkflowStubs(page);
    await forceTheme(page, theme);
    // The grid card view is the deterministic surface for the badge (the queue
    // view is suggestion-driven); pin it before navigation.
    await page.addInitScript(() => {
      try {
        localStorage.setItem('mediahub.projects.view', 'grid');
      } catch {
        /* ignore */
      }
    });
    await page.goto(`/team/${TEAM_ID}/projects`);
    await expect(page.getByTestId('project-workflow-stage-chip').first()).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('project-agents-active-chip').first()).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/07-list-badge-${theme}.png`, fullPage: true });
  });
}
