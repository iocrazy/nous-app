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

async function setupListStubs(page: Page): Promise<void> {
  await setupStubbedSession(page);
  await page.route('**/api/v1/ai-library/agents', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([AGENT]) }),
  );
  await page.route('**/api/v1/issues/?*', (r) =>
    r.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: ISSUES, total: ISSUES.length, limit: 200, offset: 0 }),
    }),
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
    await expect(page.getByText('Canvas', { exact: true })).toBeVisible();
    await expect(page.getByText('Script', { exact: true })).toBeVisible();
    await expect(page.getByText('Project', { exact: true })).toBeVisible();

    // Assignee avatar (initials of Script AI) + running badge on the live row.
    await expect(page.getByText('SC', { exact: true })).toBeVisible();
    await expect(page.getByText('running')).toBeVisible();

    await page.screenshot({ path: `${SHOTS}/list-${theme}.png`, fullPage: true });
  });
}
