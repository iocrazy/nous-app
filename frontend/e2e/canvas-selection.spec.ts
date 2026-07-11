import { expect, test } from '@playwright/test';
import { setupStubbedSession, TEAM_ID } from './helpers/stubs';

test('clicking a node selects it and enables composer Run (selection regression)', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.addInitScript(() => {
    localStorage.setItem('language', 'en');
    localStorage.setItem('mediahub.theme', 'dark');
  });
  await setupStubbedSession(page);
  await page.route('**/api/v1/canvases/c-sel', (r) =>
    r.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: {
          id: 'c-sel', project_id: 'p1', name: 'S', kind: 'smart',
          viewport_json: { x: 0, y: 0, zoom: 1 },
          nodes_json: [
            { id: 'p1', type: 'prompt', position: { x: 100, y: 100 }, data: { body: 'x', provider_slug: '', agent_id: null, run_status: 'idle', resource_refs: [] } },
          ],
          connections_json: [], node_ops_json: [], connection_ops_json: [],
          base_updated_at: '2020-01-02T00:00:00Z', created_at: '2020-01-01T00:00:00Z',
          updated_at: '2020-01-02T00:00:00Z', created_by: null,
        },
      }),
    }),
  );
  await page.route('**/api/v1/resources/search*', (r) =>
    r.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ results: [], counts: { all: 0, video: 0, image: 0, doc: 0, audio: 0, pdf: 0 }, next_cursor: null }),
    }),
  );
  await page.goto(`/team/${TEAM_ID}/canvas/c-sel`);
  await expect(page.locator('.react-flow__node')).toHaveCount(1);

  await page.locator('.react-flow__node .mh-node-head').click();
  await expect(page.locator('.react-flow__node.selected')).toHaveCount(1);
  await expect(
    page.getByLabel('Smart canvas composer').getByRole('button', { name: 'Run', exact: true }),
  ).toBeEnabled();

  // Pane click clears it again.
  await page.locator('.react-flow__pane').click({ position: { x: 600, y: 400 } });
  await expect(page.locator('.react-flow__node.selected')).toHaveCount(0);
});
