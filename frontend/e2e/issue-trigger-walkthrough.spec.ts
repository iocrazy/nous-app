import { test, expect, type Page } from '@playwright/test';
import { setupStubbedSession, TEAM_ID, USER_ID } from './helpers/stubs';

/**
 * Pre-merge visual walkthrough for PR #1405 (comment-trigger chip + suppress)
 * and the #1400 run-confirm gate, now permanently on (flags removed at go-live).
 *
 * Full-stub harness (same contract as the storyboard/script suites): no real
 * backend, no login. The chip renders the server's verdict verbatim, so the
 * stubbed preview endpoints stand in for the 6 backend predicate tests that
 * already pin the verdict logic.
 *
 * Screenshots land in test-results/walkthrough/ for human review.
 */

const SHOTS = 'test-results/walkthrough';

const AGENT = {
  id: 'agent-1',
  slug: 'script-ai',
  name: 'Script AI',
  description: null,
  icon: 'bot',
  model: 'qwen-max',
  temperature: 0.7,
};

const ISSUE = {
  id: 101,
  issue_number: 1,
  identifier: 'MH-1',
  title: 'E2E walkthrough issue',
  description: 'Verify comment-trigger chip + run-confirm gate',
  status: 'todo', // assignee + backlog/todo → Dispatch button renders
  priority: 'medium',
  team_id: null,
  project_id: null,
  parent_id: null,
  assignee_user_id: null,
  assignee_agent_id: AGENT.id,
  origin_kind: 'manual',
  origin_id: null,
  origin_fingerprint: 'fp-e2e-1',
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

const COMMENT = {
  id: 'msg-1',
  issue_id: ISSUE.id,
  kind: 'comment',
  author_user_id: USER_ID,
  author_agent_id: null,
  body: 'Existing human comment for context.',
  meta: {},
  duration_seconds: null,
  agent_run_id: null,
  liveness_state: null,
  from_status: null,
  to_status: null,
  created_at: '2026-07-02T00:00:00Z',
};

function fulfillJson(body: unknown) {
  return (route: import('@playwright/test').Route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
}

async function setupIssueStubs(page: Page): Promise<void> {
  await setupStubbedSession(page);
  // Registered after the harness catch-alls → these win.
  await page.route('**/api/v1/ai-library/agents', fulfillJson([AGENT]));
  await page.route(
    '**/api/v1/issues/?*',
    fulfillJson({ items: [ISSUE], total: 1, limit: 200, offset: 0 }),
  );
  await page.route('**/api/v1/issues/by-identifier/*', fulfillJson(ISSUE));
  await page.route(`**/api/v1/issues/${ISSUE.id}`, fulfillJson(ISSUE));
  await page.route(
    `**/api/v1/issues/${ISSUE.id}/messages`,
    fulfillJson({ messages: [COMMENT], total: 1 }),
  );
  // POST carrying the draft body (was GET until the /note keyboard flow). The
  // stub mirrors the backend predicate's two outcomes: a /note body → quiet
  // note, anything else → wake. Body-aware so the walkthrough exercises the
  // real refetch-on-boundary wiring, not a canned verdict.
  await page.route(
    `**/api/v1/issues/${ISSUE.id}/comment-trigger-preview`,
    (route) => {
      let body: string | null = null;
      try {
        body = (JSON.parse(route.request().postData() ?? '{}') as { body?: string | null }).body ?? null;
      } catch {
        body = null;
      }
      const isNote = typeof body === 'string' && /^\s*\/note(\s|$)/.test(body);
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(
          isNote
            ? { will_wake: false, agent_id: AGENT.id, is_note: true }
            : { will_wake: true, agent_id: AGENT.id, is_note: false },
        ),
      });
    },
  );
  await page.route(
    `**/api/v1/issues/${ISSUE.id}/dispatch-preview`,
    fulfillJson({ will_start: true, agent_id: AGENT.id, blocked_reason: null }),
  );
}

async function openDetail(page: Page): Promise<void> {
  await page.goto(`/team/${TEAM_ID}/todolist/${ISSUE.identifier}`);
  await expect(page.getByText(ISSUE.title).first()).toBeVisible({ timeout: 15_000 });
}

async function typeInComposer(page: Page, text: string): Promise<void> {
  const editor = page.locator('[contenteditable="true"]').last();
  await editor.click();
  await editor.pressSequentially(text, { delay: 10 });
}

const chip = (page: Page) => page.getByTestId('comment-trigger-chip');

async function forceTheme(page: Page, theme: 'dark' | 'light'): Promise<void> {
  await page.addInitScript((t) => {
    try {
      localStorage.setItem('mediahub.theme', t as string);
    } catch {
      /* ignore */
    }
  }, theme);
}

test('dark: chip arms on draft, suppresses on click, restores', async ({ page }) => {
  await setupIssueStubs(page);
  await forceTheme(page, 'dark');
  await openDetail(page);
  await page.screenshot({ path: `${SHOTS}/01-detail-dark.png`, fullPage: true });

  // Empty draft → chip hidden (disclosure only when something would be sent).
  await expect(chip(page)).toHaveCount(0);

  await typeInComposer(page, 'Please take another look at the intro.');
  await expect(chip(page)).toBeVisible();
  await expect(chip(page)).toContainText('Will start when sent');
  await expect(chip(page)).toContainText(AGENT.name);
  await page.screenshot({ path: `${SHOTS}/02-chip-armed-dark.png`, fullPage: true });

  await chip(page).click();
  await expect(chip(page)).toContainText("Won't start this time");
  await expect(chip(page)).toHaveAttribute('aria-pressed', 'true');
  await page.screenshot({ path: `${SHOTS}/03-chip-suppressed-dark.png`, fullPage: true });

  // Restore: one-shot suppress must be reversible before sending.
  await chip(page).click();
  await expect(chip(page)).toContainText('Will start when sent');
  await expect(chip(page)).toHaveAttribute('aria-pressed', 'false');
  await page.screenshot({ path: `${SHOTS}/04-chip-restored-dark.png`, fullPage: true });
});

test('dark: /note prefix flips the chip to the quiet-note state and back', async ({ page }) => {
  await setupIssueStubs(page);
  await forceTheme(page, 'dark');
  await openDetail(page);

  // A /note draft → the chip discloses a quiet note (server verdict, not local).
  await typeInComposer(page, '/note remember to check the license');
  await expect(chip(page)).toBeVisible();
  await expect(chip(page)).toContainText('Quiet note');
  await expect(chip(page)).toContainText(`won't wake ${AGENT.name}`);
  // Note is a keyboard intent, not a toggle — the chip must not be a button.
  await expect(chip(page)).toHaveAttribute('data-state', 'note');
  await page.screenshot({ path: `${SHOTS}/09-chip-note-dark.png`, fullPage: true });

  // Deleting the draft back past the boundary re-arms the wake disclosure.
  const editor = page.locator('[contenteditable="true"]').last();
  await editor.click();
  await page.keyboard.press(process.platform === 'darwin' ? 'Meta+a' : 'Control+a');
  await page.keyboard.press('Backspace');
  await typeInComposer(page, 'Normal comment again.');
  await expect(chip(page)).toContainText('Will start when sent');
  await page.screenshot({ path: `${SHOTS}/10-chip-rearmed-after-note-dark.png`, fullPage: true });
});

test('dark: run-confirm dialog renders the server verdict on Dispatch', async ({ page }) => {
  await setupIssueStubs(page);
  await forceTheme(page, 'dark');
  await openDetail(page);

  const dispatchBtn = page.getByRole('button', { name: /dispatch/i }).first();
  await expect(dispatchBtn).toBeVisible();
  await dispatchBtn.click();
  // Dialog renders the dispatch-preview verdict; assert on agent name shown.
  await expect(page.getByText(AGENT.name).last()).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/05-dispatch-confirm-dark.png`, fullPage: true });
});

test('light: chip contrast in both states', async ({ page }) => {
  await setupIssueStubs(page);
  await forceTheme(page, 'light');
  await openDetail(page);
  await page.screenshot({ path: `${SHOTS}/06-detail-light.png`, fullPage: true });

  await typeInComposer(page, 'Light theme contrast check.');
  await expect(chip(page)).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/07-chip-armed-light.png`, fullPage: true });

  await chip(page).click();
  await expect(chip(page)).toContainText("Won't start this time");
  await page.screenshot({ path: `${SHOTS}/08-chip-suppressed-light.png`, fullPage: true });
});
