// laper-style "select text → AI chat": selecting a run of script text floats an
// ink pill above the selection; clicking it opens the global AI chat with the
// selected text injected as a quoted reference (tagged with its source scene),
// and the composer focused. All AI backend calls are stubbed at the network
// layer — this exercises the wiring (selection → pill → chat), never a model.

import { expect, test, type Page } from '@playwright/test';
import {
  SCENE_ID_BASE,
  SCRIPT_URL,
  setupScriptStubs,
  wireScene,
  type WireElement,
} from './helpers/script-stubs';

/** Stub the AI-library chat endpoints so the panel gets a usable agent+session. */
async function stubAiChat(page: Page): Promise<void> {
  await page.route('**/api/v1/ai-library/agents', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        { slug: 'script_ai', name: 'Script AI', enabled: true, is_system_preset: true },
      ]),
    }),
  );
  // Sessions list (GET) → empty so the panel auto-creates one; create (POST) →
  // a session; single session (GET) → no messages.
  await page.route('**/api/v1/ai-library/agents/*/sessions**', (route) => {
    if (route.request().method() === 'POST') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ id: 'sess_e2e_1', title: 'New conversation' }),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    });
  });
  await page.route('**/api/v1/ai-library/sessions/sess_e2e_1', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ id: 'sess_e2e_1', title: 'New conversation', messages: [] }),
    }),
  );
}

const SCENE_ONE: WireElement[] = [
  { id: 'el_s1_a', type: 'action', text: 'A quiet room hums with tension.' },
  { id: 'el_s1_d', type: 'dialogue', text: 'We should leave before dawn.' },
];
const SCENE_TWO: WireElement[] = [
  { id: 'el_s2_a', type: 'action', text: 'Rain hammers the empty street.' },
];

async function openEditor(page: Page): Promise<void> {
  await setupScriptStubs(page, {
    scenes: [
      wireScene({ id: SCENE_ID_BASE + 1, sortOrder: 1, location: 'OFFICE', elements: SCENE_ONE }),
      wireScene({ id: SCENE_ID_BASE + 2, sortOrder: 2, location: 'STREET', elements: SCENE_TWO }),
    ],
  });
  await stubAiChat(page);
  await page.goto(SCRIPT_URL);
  await page.waitForSelector('[data-testid="scene-block"]', { timeout: 15_000 });
  await page.waitForTimeout(400);
}

// tiptap nests the line text below `.mh-el-editable` (NodeViewContent etc.), so
// the editable's firstChild is an element, not a text node. These helpers walk
// to the first non-empty text node before building the Range.

/** Select `[start,end)` of the text inside the editable matched by `selector`. */
async function selectWithin(
  page: Page,
  selector: string,
  start: number,
  end: number,
): Promise<void> {
  await page.evaluate(
    ({ selector, start, end }) => {
      const el = document.querySelector(selector);
      if (!el) throw new Error(`no element ${selector}`);
      const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
      let node = walker.nextNode();
      while (node && !(node.textContent || '').trim()) node = walker.nextNode();
      if (!node) throw new Error(`no text node in ${selector}`);
      const range = document.createRange();
      range.setStart(node, start);
      range.setEnd(node, end);
      const sel = window.getSelection();
      sel?.removeAllRanges();
      sel?.addRange(range);
      document.dispatchEvent(new Event('selectionchange'));
    },
    { selector, start, end },
  );
}

test('selecting script text floats the AI-chat pill above the selection', async ({ page }) => {
  await openEditor(page);

  const pill = page.getByTestId('selection-ai-chat');
  await expect(pill).toHaveCount(0); // nothing selected yet

  await selectWithin(page, '.mh-el-editable[data-el-id="el_s1_a"]', 2, 7); // "quiet"
  await expect(pill).toBeVisible();

  // The pill sits ABOVE the selected text.
  const selBox = await page.evaluate(() => {
    const r = window.getSelection()?.getRangeAt(0).getBoundingClientRect();
    return r ? { top: r.top, left: r.left, width: r.width } : null;
  });
  const pillBox = await pill.boundingBox();
  expect(selBox).not.toBeNull();
  expect(pillBox).not.toBeNull();
  expect(pillBox!.y).toBeLessThan(selBox!.top); // above the selection
});

test('clicking the pill opens AI chat with the selection quoted + scene tag', async ({ page }) => {
  await openEditor(page);

  await selectWithin(page, '.mh-el-editable[data-el-id="el_s1_a"]', 2, 7); // "quiet"
  const pill = page.getByTestId('selection-ai-chat');
  await expect(pill).toBeVisible();

  await pill.click();

  const panel = page.getByTestId('sb-panel-chat');
  await expect(panel).toBeVisible();
  // The selected text landed in the composer as a quoted reference, tagged
  // with its source scene ("S1"). Assert the language-neutral scene tag rather
  // than the localized "Selection from" label (the e2e locale is zh).
  await expect(panel).toContainText('quiet');
  await expect(panel).toContainText('S1');
});

test('the pill disappears when the selection collapses', async ({ page }) => {
  await openEditor(page);

  await selectWithin(page, '.mh-el-editable[data-el-id="el_s1_a"]', 2, 7);
  const pill = page.getByTestId('selection-ai-chat');
  await expect(pill).toBeVisible();

  await page.evaluate(() => {
    window.getSelection()?.removeAllRanges();
    document.dispatchEvent(new Event('selectionchange'));
  });
  await expect(pill).toHaveCount(0);
});

test('pressing the pill (mousedown) does not clear the selection', async ({ page }) => {
  await openEditor(page);

  await selectWithin(page, '.mh-el-editable[data-el-id="el_s1_a"]', 2, 7);
  const pill = page.getByTestId('selection-ai-chat');
  await expect(pill).toBeVisible();

  const box = await pill.boundingBox();
  await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2);
  await page.mouse.down();
  const stillSelected = await page.evaluate(
    () => (window.getSelection()?.toString() ?? '').trim(),
  );
  await page.mouse.up();
  expect(stillSelected).toBe('quiet');
});

// Each scene is its own contenteditable (ProseMirror per scene), so a native
// text selection cannot span two scenes — Chromium collapses a range whose
// endpoints live in different contenteditable roots (verified in-browser). The
// anchor-to-start-scene logic for a multi-scene range is therefore covered by
// the pure unit test (editor/selection/selectionContext.test.ts). Here we prove
// the scene tag is the ANCHOR scene's ordinal — a selection in the second scene
// tags "S2", not a hardcoded "S1".
test('the quote is tagged with the anchor scene ordinal (S2 for scene two)', async ({ page }) => {
  await openEditor(page);

  await selectWithin(page, '.mh-el-editable[data-el-id="el_s2_a"]', 0, 4); // "Rain"
  const pill = page.getByTestId('selection-ai-chat');
  await expect(pill).toBeVisible();
  await pill.click();

  const panel = page.getByTestId('sb-panel-chat');
  await expect(panel).toBeVisible();
  await expect(panel).toContainText('Rain');
  await expect(panel).toContainText('S2'); // second scene → ordinal 2
});
