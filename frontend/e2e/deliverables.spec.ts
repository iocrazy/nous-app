import { test, expect, type Page, type Route } from '@playwright/test';
import { setupStubbedSession, TEAM_ID, PARENT_PROJECT_ID, USER_ID } from './helpers/stubs';

/**
 * Pre-merge visual walkthrough for Project Workflow M2-W1 (issue-side
 * Deliverables zone). Full-stub harness (same contract as
 * issue-trigger-walkthrough): no real backend, no login. The zone renders the
 * server's source-issue file list verbatim, so the stubbed files endpoint
 * stands in for the backend upload/routing tests that pin the wiring.
 *
 * Screenshots land in test-results/deliverables/ for human review.
 */

const SHOTS = 'test-results/deliverables';
const NODE_ID = 'node-2';

// A workflow-node mirror issue: origin_kind='project_stage', project-backed,
// title "<Project> — <Stage>" (the zone reads the stage off the suffix).
const ISSUE = {
  id: 101,
  issue_number: 1,
  identifier: 'MH-1',
  title: 'Launch Film — Storyboard',
  description: 'Mirror issue for the Storyboard stage',
  status: 'in_progress',
  priority: 'medium',
  team_id: null,
  project_id: PARENT_PROJECT_ID,
  parent_id: null,
  assignee_user_id: null,
  assignee_agent_id: null,
  origin_kind: 'project_stage',
  origin_id: `project_stage:${PARENT_PROJECT_ID}:${NODE_ID}`,
  origin_fingerprint: 'fp-deliv-1',
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
  updated_at: '2026-07-01T00:00:00Z',
};

// A "filed <name>" timeline line — a comment carrying the deliverable_upload
// marker, rendered system-style by IssueChatThread.
const FILED_LINE = {
  id: 'msg-1',
  issue_id: ISSUE.id,
  kind: 'comment',
  author_user_id: USER_ID,
  author_agent_id: null,
  body: 'Filed deliverable: shot-list.pdf',
  meta: { deliverable_upload: true, filename: 'shot-list.pdf', file_id: '900' },
  duration_seconds: null,
  agent_run_id: null,
  liveness_state: null,
  from_status: null,
  to_status: null,
  created_at: '2026-07-02T00:00:00Z',
};

const FILED_FILES = [
  { id: '900', project_id: PARENT_PROJECT_ID, filename: 'shot-list.pdf', file_type: 'document', source_issue_id: '101', source_issue_identifier: 'MH-1', is_trashed: false, current_version: 1, created_at: '2026-07-02T00:00:00Z', updated_at: '2026-07-02T00:00:00Z' },
  { id: '901', project_id: PARENT_PROJECT_ID, filename: 'boards-v2.png', file_type: 'image', source_issue_id: '101', source_issue_identifier: 'MH-1', is_trashed: false, current_version: 1, created_at: '2026-07-02T00:00:00Z', updated_at: '2026-07-02T00:00:00Z' },
];

function json(body: unknown) {
  return (route: Route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
}

async function setupStubs(page: Page): Promise<void> {
  await setupStubbedSession(page);
  await page.route('**/api/v1/ai-library/agents', json([]));
  await page.route('**/api/v1/issues/?*', json({ items: [ISSUE], total: 1, limit: 200, offset: 0 }));
  await page.route('**/api/v1/issues/by-identifier/*', json(ISSUE));
  await page.route(`**/api/v1/issues/${ISSUE.id}`, json(ISSUE));
  await page.route(`**/api/v1/issues/${ISSUE.id}/messages`, json({ messages: [FILED_LINE], total: 1 }));
  await page.route(`**/api/v1/issues/${ISSUE.id}/comment-trigger-preview`, json({ will_wake: false, agent_id: null, is_note: false }));
  // The zone's source-issue file list.
  await page.route('**/api/v1/projects/*/files?*', json({ success: true, data: FILED_FILES }));
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

async function openDetail(page: Page): Promise<void> {
  await page.goto(`/team/${TEAM_ID}/todolist/${ISSUE.identifier}`);
  await expect(page.getByText(ISSUE.title).first()).toBeVisible({ timeout: 15_000 });
}

for (const theme of ['dark', 'light'] as const) {
  test(`${theme}: Deliverables zone lists filed files with routing hint`, async ({ page }) => {
    await setupStubs(page);
    await forceTheme(page, theme);
    await openDetail(page);

    const zone = page.getByTestId('deliverables-zone');
    await expect(zone).toBeVisible();
    // Mirror issue → hint names both the project and the stage.
    await expect(page.getByTestId('deliverables-dropzone')).toContainText('Storyboard');
    // Both filed files render with a Filed chip.
    await expect(page.getByTestId('deliverables-file-row')).toHaveCount(2);
    await expect(zone).toContainText('shot-list.pdf');
    // Timeline shows the system-style "filed" line.
    await expect(page.getByTestId('deliverable-filed-row').first()).toBeVisible();

    await page.screenshot({ path: `${SHOTS}/01-deliverables-${theme}.png`, fullPage: true });
  });
}
