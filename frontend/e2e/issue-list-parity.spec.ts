import { test, expect, type Page } from '@playwright/test';
import { setupStubbedSession, TEAM_ID, USER_ID } from './helpers/stubs';

/**
 * List-view parity with the approved mockup: page header ("Issues" + team
 * name + the C / kbd hint cluster), origin-module tag chips, assignee
 * avatars and relative times on rows. Full-stub harness — no backend.
 */

const SHOTS = 'test-results/list-parity';

const AGENT = {
  id: 'agent-1',
  slug: 'script-ai',
  name: 'Script AI',
  description: null,
  icon: 'bot',
  model: 'qwen-max',
  temperature: 0.7,
};

function issue(over: Record<string, unknown>) {
  return {
    issue_number: 1,
    title: 'Issue',
    description: null,
    status: 'todo',
    priority: 'medium',
    team_id: null,
    project_id: null,
    parent_id: null,
    assignee_user_id: null,
    assignee_agent_id: null,
    origin_kind: 'manual',
    origin_id: null,
    origin_fingerprint: 'fp',
    billing_code: null,
    created_by_user_id: USER_ID,
    created_by_agent_id: null,
    dbos_workflow_id: null,
    execution_locked_at: null,
    execution_state: null,
    request_depth: 0,
    started_at: null,
    completed_at: null,
    cancelled_at: null,
    hidden_at: null,
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-15T00:00:00Z',
    ...over,
  };
}

const ISSUES = [
  issue({ id: 1, issue_number: 1, identifier: 'MH-1', title: 'Canvas group v2 — thumbnail grid', origin_kind: 'manual', origin_id: 'canvas:99887766554433', assignee_agent_id: AGENT.id, status: 'in_progress', dbos_workflow_id: 'wf-1' }),
  issue({ id: 2, issue_number: 2, identifier: 'MH-2', title: 'Scene heading tokens read as slug', origin_id: 'scene:1234', status: 'in_review' }),
  issue({ id: 3, issue_number: 3, identifier: 'MH-3', title: 'Film A — Script stage', origin_kind: 'project_stage', origin_id: 'project_stage:100:20', status: 'todo' }),
  issue({ id: 4, issue_number: 4, identifier: 'MH-4', title: 'Plain manual issue, no chip', status: 'backlog' }),
];

const OTHER_USER = '00000000-0000-4000-8000-0000000000ff';

// Scope-projection fixture: created-by / assignee-to spread across me, a
// teammate, and the agent so the My / Agent client filters are observable.
const SCOPE_ISSUES = [
  issue({ id: 10, issue_number: 10, identifier: 'MH-10', title: 'My own task', created_by_user_id: USER_ID, status: 'todo' }),
  issue({ id: 11, issue_number: 11, identifier: 'MH-11', title: 'Assigned to me', created_by_user_id: OTHER_USER, assignee_user_id: USER_ID, status: 'todo' }),
  issue({ id: 12, issue_number: 12, identifier: 'MH-12', title: 'Teammate task', created_by_user_id: OTHER_USER, assignee_user_id: OTHER_USER, status: 'todo' }),
  issue({ id: 13, issue_number: 13, identifier: 'MH-13', title: 'Agent handled task', created_by_user_id: OTHER_USER, assignee_agent_id: AGENT.id, status: 'in_progress' }),
];

// Project-grouping fixture: two projects + one loose issue.
const GROUP_ISSUES = [
  issue({ id: 20, issue_number: 20, identifier: 'MH-20', title: 'Alpha issue one', project_id: 500, status: 'todo' }),
  issue({ id: 21, issue_number: 21, identifier: 'MH-21', title: 'Alpha issue two', project_id: 500, status: 'in_progress' }),
  issue({ id: 22, issue_number: 22, identifier: 'MH-22', title: 'Beta issue', project_id: 600, status: 'todo' }),
  issue({ id: 23, issue_number: 23, identifier: 'MH-23', title: 'Loose issue', project_id: null, status: 'backlog' }),
];

const GROUP_PROJECTS = [
  { id: '500', name: 'Alpha' },
  { id: '600', name: 'Beta' },
];

async function setupListStubs(
  page: Page,
  issues: Record<string, unknown>[] = ISSUES,
  projects: { id: string; name: string }[] = [],
): Promise<void> {
  await setupStubbedSession(page);
  await page.route('**/api/v1/ai-library/agents', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([AGENT]) }),
  );
  // fetchProjects expects a {data: Project[]} envelope; minimal {id,name} rows
  // are enough for name resolution + the picker.
  await page.route('**/api/v1/projects?*', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ data: projects }) }),
  );
  await page.route('**/api/v1/projects', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ data: projects }) }),
  );
  await page.route('**/api/v1/issues/?*', (r) =>
    r.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: issues, total: issues.length, limit: 200, offset: 0 }),
    }),
  );
}

async function forceTheme(page: Page, theme: 'dark' | 'light'): Promise<void> {
  await page.addInitScript((t) => {
    try {
      localStorage.setItem('mediahub.theme', t as string);
      // i18n defaults to 'zh' when no `language` key is stored (see i18n.ts).
      // The header assertions below read English, and they only used to pass
      // because the title was a hardcoded literal that ignored the locale.
      localStorage.setItem('language', 'en');
    } catch {
      /* ignore */
    }
  }, theme);
}

for (const theme of ['dark', 'light'] as const) {
  test(`${theme}: header + module tags + avatar match the mockup`, async ({ page }) => {
    await setupListStubs(page);
    await forceTheme(page, theme);
    await page.goto(`/team/${TEAM_ID}/todolist`);
    await expect(page.getByText('Canvas group v2 — thumbnail grid')).toBeVisible({ timeout: 15_000 });

    // Page header: title + kbd hint cluster (the mockup's top row).
    await expect(page.getByRole('heading', { name: /Issues/ })).toBeVisible();
    await expect(page.getByTestId('issues-kbd-hints')).toBeVisible();

    // Origin-module chips: one per content origin, none for plain manual.
    // Targeted by their `Origin: …` title so the assertion can't collide with
    // the Group-by "Project" toggle or a project pill elsewhere on the page.
    await expect(page.getByTitle('Origin: Canvas')).toBeVisible();
    await expect(page.getByTitle('Origin: Script')).toBeVisible();
    await expect(page.getByTitle('Origin: Project')).toBeVisible();

    // Assignee avatar (initials of Script AI) + running badge on the live row.
    await expect(page.getByText('SC', { exact: true })).toBeVisible();
    await expect(page.getByText('running')).toBeVisible();

    await page.screenshot({ path: `${SHOTS}/list-${theme}.png`, fullPage: true });
  });
}

for (const theme of ['dark', 'light'] as const) {
  test(`${theme}: scope pills project the team list (My / Agent) client-side`, async ({ page }) => {
    await setupListStubs(page, SCOPE_ISSUES);
    await forceTheme(page, theme);
    await page.goto(`/team/${TEAM_ID}/todolist`);

    // Team scope (default): the whole team list, and the "Creates in …" badge.
    await expect(page.getByText('My own task')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText('Teammate task')).toBeVisible();
    await expect(page.getByText('Agent handled task')).toBeVisible();
    await expect(page.getByTestId('creates-in-badge')).toContainText('Creates in');

    // My Issues: created-by-me ∪ assigned-to-me; teammate + agent-only rows drop.
    await page.getByRole('button', { name: 'My Issues' }).click();
    await expect(page.getByText('My own task')).toBeVisible();
    await expect(page.getByText('Assigned to me')).toBeVisible();
    await expect(page.getByText('Teammate task')).toHaveCount(0);
    await expect(page.getByText('Agent handled task')).toHaveCount(0);

    // Agent scope: pick the agent → only its assigned issue; badge is read-only
    // and the New Issue button disappears.
    await page.getByTestId('scope-pills').getByRole('combobox').selectOption(AGENT.id);
    await expect(page.getByText('Agent handled task')).toBeVisible();
    await expect(page.getByText('My own task')).toHaveCount(0);
    await expect(page.getByTestId('creates-in-badge')).toContainText('Read-only view');
    await expect(page.getByRole('button', { name: 'New Issue' })).toHaveCount(0);

    await page.screenshot({ path: `${SHOTS}/scope-${theme}.png`, fullPage: true });
  });

  test(`${theme}: Group by Project renders project ⊃ issue tree`, async ({ page }) => {
    await setupListStubs(page, GROUP_ISSUES, GROUP_PROJECTS);
    await forceTheme(page, theme);
    await page.goto(`/team/${TEAM_ID}/todolist`);
    await expect(page.getByText('Alpha issue one')).toBeVisible({ timeout: 15_000 });

    // Flip Group: Status → Project.
    await page.getByRole('button', { name: 'Project', exact: true }).click();

    // Project group headers (names resolved from the projects stub) + the
    // catch-all "No project" bucket for the loose issue.
    const groups = page.getByTestId('project-group');
    await expect(groups).toHaveCount(3);
    await expect(page.getByText('Alpha', { exact: true })).toBeVisible();
    await expect(page.getByText('Beta', { exact: true })).toBeVisible();
    await expect(page.getByText('No project', { exact: true })).toBeVisible();

    // Rows still render under their group.
    await expect(page.getByText('Alpha issue two')).toBeVisible();
    await expect(page.getByText('Loose issue')).toBeVisible();

    await page.screenshot({ path: `${SHOTS}/group-project-${theme}.png`, fullPage: true });
  });
}
