// The right-click menu must be scoped by WHERE it opens: a paragraph (element)
// row offers only paragraph-level actions (delete this block / create issue),
// while scene-level actions (delete scene / MOVE SCENE) live only on the scene
// heading row. Mixing "Move scene" into a paragraph's menu let a writer arm the
// whole-scene move overlay while thinking they were acting on one paragraph,
// then a drag on the scene body moved the entire scene (the reported bug).
//
// Items are keyed via `data-key` (SceneContextMenu) so this asserts intent, not
// i18n text.

import { expect, test } from '@playwright/test';
import {
  SCENE_ID_BASE,
  SCRIPT_URL,
  setupScriptStubs,
  wireScene,
  type WireElement,
} from './helpers/script-stubs';

const els: WireElement[] = [
  { id: 'el_m0001', type: 'action', text: 'First action line.' },
  { id: 'el_m0002', type: 'action', text: 'Second action line.' },
];

const SCENES = [
  wireScene({ id: SCENE_ID_BASE + 1, sortOrder: 1, location: 'ONE', elements: els }),
  wireScene({ id: SCENE_ID_BASE + 2, sortOrder: 2, location: 'TWO', elements: els }),
];

async function menuKeys(page: import('@playwright/test').Page): Promise<string[]> {
  return page.$$eval('[role="menu"] [data-key]', (nodes) =>
    nodes.map((n) => (n as HTMLElement).dataset.key || ''),
  );
}

test('paragraph-row menu excludes scene-level actions; heading-row menu keeps them', async ({
  page,
}) => {
  await setupScriptStubs(page, { scenes: SCENES, format: 'hollywood' });
  await page.goto(SCRIPT_URL);
  await page.waitForSelector('[data-testid="scene-block"]', { timeout: 15_000 });
  await page.waitForTimeout(500);

  // ── Element row ──────────────────────────────────────────────────────────
  await page.locator('[data-el-id="el_m0002"]').first().click({ button: 'right' });
  await page.waitForSelector('[role="menu"]', { timeout: 5_000 });
  const elMenu = await menuKeys(page);
  expect(elMenu, 'paragraph menu keeps block-level actions').toContain('delete-block');
  expect(elMenu, 'paragraph menu keeps create-issue').toContain('create-issue');
  expect(elMenu, 'paragraph menu must NOT offer whole-scene move').not.toContain('move-scene');
  expect(elMenu, 'paragraph menu must NOT offer delete-scene').not.toContain('delete-scene');

  // Dismiss and open the heading-row menu.
  await page.keyboard.press('Escape');
  await page.waitForTimeout(150);
  await page.locator('.mh-scene-headrow').first().click({ button: 'right' });
  await page.waitForSelector('[role="menu"]', { timeout: 5_000 });
  const headMenu = await menuKeys(page);
  expect(headMenu, 'heading menu keeps whole-scene move').toContain('move-scene');
  expect(headMenu, 'heading menu keeps delete-scene').toContain('delete-scene');
  expect(headMenu, 'heading menu keeps create-issue').toContain('create-issue');
  expect(headMenu, 'heading has no single-block to delete').not.toContain('delete-block');
});
