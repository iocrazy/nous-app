import { test, expect, type Page, type Route } from '@playwright/test';
import { setupStubbedSession, TEAM_ID, FIXTURE_IMAGE_URL } from './helpers/stubs';

/**
 * Pre-merge visual walkthrough for Ideation (Project Workflow M1.5).
 *
 * Full-stub harness (same contract as the workflow / issue-trigger suites): no
 * real backend, no login. The board reads /api/v1/ideation/topics; the create-
 * from-topic flow opens the existing CreateProjectModal prefilled with the
 * topic. Screenshots land in test-results/ideation/ for human review.
 */

const SHOTS = 'test-results/ideation';

const TOPICS = [
  {
    id: 'topic-1',
    team_id: TEAM_ID,
    title: 'Spring Launch Teaser',
    cover_url: FIXTURE_IMAGE_URL,
    excerpt: 'A short hype clip for the spring drop.',
    status: 'candidate',
    note_id: null,
    resource_id: null,
    media_id: null,
    inspiration_topic_id: null,
    created_by: null,
    created_at: '2026-07-19T00:00:00Z',
    updated_at: '2026-07-19T00:00:00Z',
  },
  {
    id: 'topic-2',
    team_id: TEAM_ID,
    title: 'Behind the scenes: studio day',
    cover_url: null,
    excerpt: 'From an inspiration note.',
    status: 'shortlisted',
    note_id: 'note-9',
    resource_id: null,
    media_id: null,
    inspiration_topic_id: null,
    created_by: null,
    created_at: '2026-07-18T00:00:00Z',
    updated_at: '2026-07-18T00:00:00Z',
  },
  {
    id: 'topic-3',
    team_id: TEAM_ID,
    title: 'Product unboxing',
    cover_url: FIXTURE_IMAGE_URL,
    excerpt: 'Sourced from the download library.',
    status: 'produced',
    note_id: null,
    resource_id: 'res-3',
    media_id: null,
    inspiration_topic_id: null,
    created_by: null,
    created_at: '2026-07-17T00:00:00Z',
    updated_at: '2026-07-17T00:00:00Z',
  },
];

function json(body: unknown) {
  return (route: Route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
}

async function setupIdeationStubs(page: Page): Promise<void> {
  await setupStubbedSession(page);
  // Registered after the harness catch-alls → these win.
  await page.route('**/api/v1/projects?*', json({ success: true, data: [] }));
  await page.route('**/api/v1/ideation/topics?*', json({ success: true, data: TOPICS }));
  // Create-topic / patch land back on the same collection prefix.
  await page.route('**/api/v1/ideation/topics/*', json({ success: true, data: TOPICS[0] }));
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

async function openIdeation(page: Page): Promise<void> {
  await page.goto(`/team/${TEAM_ID}/projects`);
  await page.getByTestId('ideation-entry').click();
  await expect(page.getByTestId('ideation-board')).toBeVisible({ timeout: 15_000 });
}

for (const theme of ['dark', 'light'] as const) {
  test(`${theme}: ideation board renders topics + tabs`, async ({ page }) => {
    await setupIdeationStubs(page);
    await forceTheme(page, theme);
    await openIdeation(page);

    // The three fixture topics render as cards.
    await expect(page.getByTestId('topic-card')).toHaveCount(3);
    await expect(page.getByText('Spring Launch Teaser')).toBeVisible();
    // Tabs present (All + one per status).
    await expect(page.getByTestId('ideation-tab-shortlisted')).toBeVisible();

    await page.screenshot({ path: `${SHOTS}/01-board-${theme}.png`, fullPage: true });

    // Filter to the Produced tab → only the produced topic remains.
    await page.getByTestId('ideation-tab-produced').click();
    await expect(page.getByTestId('topic-card')).toHaveCount(1);
    await expect(page.getByText('Product unboxing')).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/02-produced-tab-${theme}.png`, fullPage: true });
  });
}

test('dark: create project from topic opens the modal prefilled', async ({ page }) => {
  await setupIdeationStubs(page);
  await forceTheme(page, 'dark');
  await openIdeation(page);

  // Click "Create project" on the first card → modal opens with the from-topic
  // chip and the name pre-filled from the topic title.
  await page.getByTestId('topic-create-project').first().click();
  await expect(page.getByTestId('create-project-modal')).toBeVisible();
  await expect(page.getByTestId('from-topic-chip')).toBeVisible();
  await expect(page.getByTestId('from-topic-chip')).toContainText('Spring Launch Teaser');
  await expect(page.getByTestId('project-name-input')).toHaveValue('Spring Launch Teaser');

  await page.screenshot({ path: `${SHOTS}/03-create-from-topic-dark.png`, fullPage: true });
});

test('dark: new-topic dialog opens from the toolbar', async ({ page }) => {
  await setupIdeationStubs(page);
  await forceTheme(page, 'dark');
  await openIdeation(page);

  await page.getByTestId('ideation-new-topic').click();
  await expect(page.getByTestId('ideation-new-topic-input')).toBeVisible();
  await page.getByTestId('ideation-new-topic-input').fill('A fresh idea');
  await page.screenshot({ path: `${SHOTS}/04-new-topic-dark.png`, fullPage: true });
});
