// Cursor-style version diff: opening Compare on a saved version must render the
// three change states (added / removed / word-level changed / moved), aggregate
// the scene-set additions into a collapsible group, attribute each change to an
// author chip, and jump the live sheet when a change is clicked.
//
// Fully stubbed (commits + diff responses) — no real backend, no login — so the
// browser-behaviour assertions (inline word spans, jump-flash) run against the
// real component, not a jsdom approximation.

import { expect, test } from '@playwright/test';
import { SCENE_ID_BASE, SCRIPT_ID, SCRIPT_URL, setupScriptStubs, wireScene, type WireElement } from './helpers/script-stubs';

const SCENE_ID = SCENE_ID_BASE + 1;

const ELEMENTS: WireElement[] = [
  { id: 'el_c', type: 'dialogue', text: 'After text' },
  { id: 'el_m', type: 'action', text: 'Moved line' },
];

const SCENES = [wireScene({ id: SCENE_ID, sortOrder: 1, location: 'KITCHEN', elements: ELEMENTS })];

const COMMIT = {
  id: '900000000000001',
  script_id: Number(SCRIPT_ID),
  message: 'First draft',
  watermarks: { [String(SCENE_ID)]: 1 },
  scene_ids: [],
  created_by: 'user-1',
  author_name: 'Alice',
  created_at: '2020-01-02T00:00:00Z',
};

const DIFF = {
  scenes: [
    {
      scene_id: String(SCENE_ID),
      author: 'copilot',
      elements: [
        { kind: 'added', id: 'el_a', before: null, after: { id: 'el_a', type: 'action', text: 'New line' }, actor: 'user-1' },
        { kind: 'removed', id: 'el_r', before: { id: 'el_r', type: 'action', text: 'Old line' }, after: null, actor: 'copilot' },
        {
          kind: 'changed',
          id: 'el_c',
          before: { id: 'el_c', type: 'dialogue', text: 'Before text' },
          after: { id: 'el_c', type: 'dialogue', text: 'After text' },
          actor: 'user-1',
        },
        { kind: 'moved', id: 'el_m', before: { id: 'el_m', type: 'action', text: 'Moved line' }, after: { id: 'el_m', type: 'action', text: 'Moved line' }, actor: 'user-1' },
      ],
    },
  ],
  scenes_added: [{ id: '999000000000001', sort_order: 2, heading_int_ext: 'EXT', location_text: 'Park', author: 'user-1' }],
  scenes_removed: [],
  authors: { 'user-1': 'Alice' },
};

test('Compare renders the Cursor-style three-state diff with authors and jump', async ({ page }) => {
  await setupScriptStubs(page, { scenes: SCENES, format: 'hollywood' });
  await page.route(`**/api/v1/scripts/${SCRIPT_ID}/commits`, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true, data: [COMMIT] }) }),
  );
  await page.route(`**/api/v1/scripts/${SCRIPT_ID}/commits/*/diff*`, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true, data: DIFF }) }),
  );

  await page.goto(SCRIPT_URL);
  await page.waitForSelector('[data-testid="scene-block"]', { timeout: 15_000 });

  // The version list renders the saved commit with its author.
  const versionItem = page.locator('[data-testid="version-item"]').first();
  await expect(versionItem).toBeVisible();
  await expect(versionItem.locator('.mh-version-author')).toHaveText('Alice');

  // Open Compare → the diff rail mounts. Action pills reveal on hover.
  await versionItem.hover();
  await versionItem.locator('.mh-version-action').first().click();
  await expect(page.locator('[data-testid="version-diff"]')).toBeVisible();

  // Three states present, in document order.
  const changes = page.locator('[data-testid="diff-change"]');
  await expect(changes).toHaveCount(4);
  await expect(changes.nth(0)).toHaveAttribute('data-kind', 'added');
  await expect(changes.nth(1)).toHaveAttribute('data-kind', 'removed');
  await expect(changes.nth(2)).toHaveAttribute('data-kind', 'changed');
  await expect(changes.nth(3)).toHaveAttribute('data-kind', 'moved');

  // The changed element shows a word-level inline diff (old struck, new highlit).
  const changed = changes.nth(2);
  await expect(changed.locator('.mh-w-del')).toHaveText('Before');
  await expect(changed.locator('.mh-w-ins')).toHaveText('After');

  // Scene-set additions are aggregated into a collapsible group with a count.
  const group = page.locator('[data-testid="diff-sceneset-added"]');
  await expect(group).toBeVisible();
  await expect(group.locator('.mh-diff-scene-count')).toHaveText('1');

  // Author chips: server-resolved name + the AI label for the copilot change.
  const chips = page.locator('[data-testid="diff-author"]');
  await expect(chips.filter({ hasText: 'Alice' }).first()).toBeVisible();
  await expect(chips.filter({ hasText: 'AI' }).first()).toBeVisible();

  // Clicking the changed row jumps the live sheet to that element (flash pulse).
  await changed.click();
  await expect(page.locator('[data-el-id="el_c"]')).toHaveClass(/mh-diff-jump-flash/);
});
