// e2e/canvas-feel.spec.ts
// Infinite-Canvas parity Phase 3 (G6) against the real production build:
//  - edge scissors: selecting a wire reveals a midpoint cut button; clicking
//    removes the connection
//  - Arrange: one click lays the graph out left-to-right by dependency rank
//  - bare `z`: toggles the zoom-out overview and returns to the prior viewport

import { expect, test, type Page } from '@playwright/test';
import { setupStubbedSession, TEAM_ID } from './helpers/stubs';

const CANVAS = {
  id: 'c-feel',
  project_id: 'p1',
  name: 'Feel Canvas',
  kind: 'smart',
  viewport_json: { x: 0, y: 0, zoom: 1 },
  nodes_json: [
    { id: 'shot1', type: 'shot', position: { x: 620, y: 420 }, data: { title: 'Shot', reference_resource_ids: [], notes: '' } },
    { id: 'p1', type: 'prompt', position: { x: 60, y: 60 }, data: { body: 'Prompt', provider_slug: null, agent_id: null, run_status: 'idle', resource_refs: [] } },
    { id: 'out1', type: 'output', position: { x: 320, y: 420 }, data: { kind: 'text', resource_id: null, preview_text: 'result', preview_url: null, crop_region: null } },
  ],
  connections_json: [
    { id: 'e1', source: 'shot1', target: 'p1', sourceHandle: null, targetHandle: null },
    { id: 'e2', source: 'p1', target: 'out1', sourceHandle: null, targetHandle: null },
  ],
  node_ops_json: [],
  connection_ops_json: [],
  base_updated_at: '2020-01-02T00:00:00Z',
  created_at: '2020-01-01T00:00:00Z',
  updated_at: '2020-01-02T00:00:00Z',
  created_by: null,
};

async function setupStubs(page: Page): Promise<void> {
  await page.route('**/api/v1/canvases/c-feel', (route) => {
    if (route.request().method() !== 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ success: true, data: CANVAS }),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: CANVAS }),
    });
  });
  await page.route('**/api/v1/canvases/c-feel/ops', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: { ...CANVAS } }),
    }),
  );
  await page.route('**/api/v1/resources/search*', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        results: [],
        counts: { all: 0, video: 0, image: 0, doc: 0, audio: 0, pdf: 0 },
        next_cursor: null,
      }),
    }),
  );
}

async function openCanvas(page: Page): Promise<void> {
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.addInitScript(() => {
    try {
      localStorage.setItem('language', 'en');
      localStorage.setItem('mediahub.theme', 'dark');
    } catch {
      /* localStorage unavailable */
    }
  });
  await setupStubbedSession(page);
  await setupStubs(page);
  await page.goto(`/team/${TEAM_ID}/canvas/c-feel`);
  await expect(page.locator('.react-flow__node')).toHaveCount(3);
}

test('selecting an edge shows the scissors; clicking cuts the wire', async ({ page }) => {
  await openCanvas(page);
  await expect(page.locator('.react-flow__edge')).toHaveCount(2);

  // Click a point ON the wire (the bezier's bbox centre is off the curve, so
  // a plain locator click misses the stroke's hit area).
  const mid = await page
    .locator('.react-flow__edge[data-id="e1"] .react-flow__edge-interaction')
    .evaluate((el) => {
      const path = el as SVGGeometryElement;
      const p = path.getPointAtLength(path.getTotalLength() / 2);
      const ctm = path.getScreenCTM();
      if (!ctm) throw new Error('no screen CTM for edge path');
      return { x: ctm.a * p.x + ctm.c * p.y + ctm.e, y: ctm.b * p.x + ctm.d * p.y + ctm.f };
    });
  // P1-8: every edge carries a dimmed always-on scissors at its midpoint —
  // one click at the midpoint cuts the wire directly (no select-first step;
  // this is exactly Infinite's conn-cut flow).
  await expect(page.getByRole('button', { name: 'Cut connection' })).toHaveCount(2);
  await page.mouse.click(mid.x, mid.y);
  await expect(page.locator('.react-flow__edge')).toHaveCount(1);
  await expect(page.locator('.react-flow__edge[data-id="e1"]')).toHaveCount(0);
});

test('Arrange lays the graph out left-to-right by rank', async ({ page }) => {
  await openCanvas(page);

  await page.getByRole('button', { name: 'Arrange' }).click();

  const xOf = async (id: string) => {
    const transform = await page
      .locator(`.react-flow__node[data-id="${id}"]`)
      .evaluate((el) => (el as HTMLElement).style.transform);
    const m = /translate\(([-\d.]+)px/.exec(transform);
    return m ? parseFloat(m[1]) : NaN;
  };
  // shot1 → p1 → out1 must end up in ascending x order.
  const [xs, xp, xo] = await Promise.all([xOf('shot1'), xOf('p1'), xOf('out1')]);
  expect(xs).toBeLessThan(xp);
  expect(xp).toBeLessThan(xo);

  await page.screenshot({ path: 'e2e-artifacts/canvas-feel-arrange.png', fullPage: true });
});

test('mod+A then mod+D duplicates the whole graph with its wiring', async ({ page }) => {
  await openCanvas(page);
  await expect(page.locator('.react-flow__edge')).toHaveCount(2);

  await page.locator('.react-flow').click({ position: { x: 30, y: 200 } });
  await page.keyboard.press('ControlOrMeta+a');
  await page.keyboard.press('ControlOrMeta+d');

  await expect(page.locator('.react-flow__node')).toHaveCount(6);
  await expect(page.locator('.react-flow__edge')).toHaveCount(4);
});

test('Export downloads the selection; Import appends it back re-id (G5)', async ({ page }) => {
  await openCanvas(page);

  await page.locator('.react-flow').click({ position: { x: 30, y: 200 } });
  await page.keyboard.press('ControlOrMeta+a');

  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export' }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/^workflow-3nodes-.*\.json$/);
  const path = await download.path();

  // Round-trip: import the very file we just exported.
  await page
    .getByTestId('workflow-import-input')
    .setInputFiles(path!);
  await expect(page.locator('.react-flow__node')).toHaveCount(6);
  await expect(page.locator('.react-flow__edge')).toHaveCount(4);
});

test('knife mode: x → drag across a wire cuts it; Escape exits (②-1)', async ({ page }) => {
  await openCanvas(page);
  await expect(page.locator('.react-flow__edge')).toHaveCount(2);

  await page.locator('.react-flow').click({ position: { x: 30, y: 200 } });
  await page.keyboard.press('x');
  const overlay = page.getByTestId('knife-overlay');
  await expect(overlay).toBeVisible();

  // Slice across e2 (p1 at 60,60 → out1 at 320,420): drag through the
  // midpoint region between the two nodes.
  const mid = await page
    .locator('.react-flow__edge[data-id="e2"] .react-flow__edge-path')
    .evaluate((el) => {
      const path = el as SVGGeometryElement;
      const p = path.getPointAtLength(path.getTotalLength() / 2);
      const ctm = path.getScreenCTM()!;
      return { x: ctm.a * p.x + ctm.c * p.y + ctm.e, y: ctm.b * p.x + ctm.d * p.y + ctm.f };
    });
  await page.mouse.move(mid.x - 40, mid.y - 40);
  await page.mouse.down();
  await page.mouse.move(mid.x + 40, mid.y + 40, { steps: 8 });
  await page.mouse.up();

  await expect(page.locator('.react-flow__edge')).toHaveCount(1);
  await expect(page.locator('.react-flow__edge[data-id="e2"]')).toHaveCount(0);

  await page.keyboard.press('Escape');
  await expect(page.getByTestId('knife-overlay')).toHaveCount(0);
});

test('Group wraps selection; dragging the group moves members (②-3)', async ({ page }) => {
  await openCanvas(page);

  await page.locator('.react-flow').click({ position: { x: 30, y: 200 } });
  await page.keyboard.press('ControlOrMeta+a');
  await page.getByRole('button', { name: 'Group' }).click();

  const group = page.getByTestId('smart-group-node');
  await expect(group).toBeVisible();

  // The group's box comes from `style` (grouping.ts) — if the RF adapter
  // strips it the region collapses to a speck and this drag would grab a
  // member instead (the silent ②-3 break this test failed to catch).
  const groupBox = (await group.boundingBox())!;
  expect(groupBox.width).toBeGreaterThan(200);
  expect(groupBox.height).toBeGreaterThan(200);

  // Drag the group from an empty patch of its own background — NOT its "top
  // edge, horizontal center" (the old coordinates). This canvas's seeded
  // layout (p1 top-left, shot1/out1 bottom-right — see CANVAS above) makes
  // the group wide enough that its top-center point lands squarely on
  // TopNodeBar (`aria-label="Canvas node bar"`), a fixed-position toolbar
  // absolutely positioned OVER the canvas pane (top-4, horizontally
  // centered, z-30) — confirmed via elementFromPoint: the old click hit a
  // toolbar <button>, never the group node, so the "drag" moved nothing and
  // the assertion saw a 0px delta. Pick a point empirically clear of every
  // overlapping surface: left of every member node's left edge (inside the
  // group's own padding) and vertically between p1's bottom and the
  // shot1/out1 row's top (both members are far from the group's edges here).
  const before = await page
    .locator('.react-flow__node[data-id="p1"]')
    .evaluate((el) => (el as HTMLElement).getBoundingClientRect().x);
  const box = (await group.boundingBox())!;
  const p1Rect = (await page.locator('.react-flow__node[data-id="p1"]').boundingBox())!;
  const shot1Rect = (await page.locator('.react-flow__node[data-id="shot1"]').boundingBox())!;
  const out1Rect = (await page.locator('.react-flow__node[data-id="out1"]').boundingBox())!;
  const dragX = box.x + 10;
  const dragY = (p1Rect.y + p1Rect.height + Math.min(shot1Rect.y, out1Rect.y)) / 2;
  await page.mouse.move(dragX, dragY);
  await page.mouse.down();
  await page.mouse.move(dragX + 120, dragY, { steps: 6 });
  await page.mouse.up();
  const after = await page
    .locator('.react-flow__node[data-id="p1"]')
    .evaluate((el) => (el as HTMLElement).getBoundingClientRect().x);
  expect(after - before).toBeGreaterThan(80);

  // The drag left the group selected — Ungroup is live.
  await page.getByRole('button', { name: 'Ungroup' }).click();
  await expect(page.getByTestId('smart-group-node')).toHaveCount(0);
});

test('workflow Save → Library import round trip (②-4)', async ({ page }) => {
  await openCanvas(page);

  let savedBody: string | null = null;
  await page.route('**/api/v1/resources/upload**', async (route) => {
    const req = route.request();
    savedBody = req.postData();
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: { id: '501', filename: 'workflow-3nodes.json' } }),
    });
  });
  await page.route('**/api/v1/resources/search*', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        results: [{ id: '501', name: 'workflow-3nodes.json', kind: 'doc' }],
        counts: { all: 1, video: 0, image: 0, doc: 1, audio: 0, pdf: 0 },
        next_cursor: null,
      }),
    }),
  );
  await page.route('**/api/v1/resources/501/file*', (route) => {
    // Serve back what Save uploaded — a true round trip.
    const idx = savedBody?.indexOf('{');
    const json = idx != null && idx >= 0 ? savedBody!.slice(idx, savedBody!.lastIndexOf('}') + 1) : '{}';
    return route.fulfill({ status: 200, contentType: 'application/json', body: json });
  });

  await page.locator('.react-flow').click({ position: { x: 30, y: 200 } });
  await page.keyboard.press('ControlOrMeta+a');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByRole('status')).toContainText('Saved as workflow');

  await page.getByRole('button', { name: 'Workflows', exact: true }).click();
  // Scope to the picker: the save notice carries the same filename text and
  // sits on the same anchor — an unscoped getByText can hit the toast and
  // "import" nothing (how this test shipped red).
  await page
    .getByTestId('workflow-library-picker')
    .getByText('workflow-3nodes.json')
    .click();
  await expect(page.locator('.react-flow__node')).toHaveCount(6);
});

test('bare z toggles the overview and returns', async ({ page }) => {
  await openCanvas(page);

  const viewportTransform = () =>
    page
      .locator('.react-flow__viewport')
      .evaluate((el) => (el as HTMLElement).style.transform);

  const before = await viewportTransform();
  await page.locator('.react-flow').click({ position: { x: 30, y: 200 } });
  await page.keyboard.press('z');
  await expect
    .poll(viewportTransform, { message: 'overview should change the viewport' })
    .not.toBe(before);

  await page.keyboard.press('z');
  await expect
    .poll(viewportTransform, { message: 'second z should restore the viewport' })
    .toBe(before);
});

test('wheel zoom-out passes the old React Flow 0.5 floor (P0-6)', async ({ page }) => {
  await openCanvas(page);

  const zoomOf = () =>
    page.locator('.react-flow__viewport').evaluate((el) => {
      const m = /scale\(([\d.]+)\)/.exec((el as HTMLElement).style.transform);
      return m ? Number(m[1]) : 1;
    });

  // Wheel-out repeatedly over the pane; RF default minZoom=0.5 clamps here,
  // the Infinite-parity engine must keep going well past it.
  const pane = page.locator('.react-flow__pane');
  const box = (await pane.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  for (let i = 0; i < 25; i++) {
    await page.mouse.wheel(0, 400);
  }
  await expect
    .poll(zoomOf, { message: 'zoom should go below the 0.5 RF default floor' })
    .toBeLessThan(0.45);
});

test('node drag is free of the 8px lattice (P2-6)', async ({ page }) => {
  await openCanvas(page);
  const node = page.locator('.react-flow__node[data-id="p1"]');
  const box = (await node.boundingBox())!;
  // Drag by a deliberately non-multiple-of-8 delta, well away from other
  // nodes so the drop-time alignment snap has nothing to bite on.
  await page.mouse.move(box.x + box.width / 2, box.y + 8);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 13, box.y + 8 + 5, { steps: 4 });
  await page.mouse.up();
  const transform = await node.evaluate((el) => (el as HTMLElement).style.transform);
  const m = /translate\(([-\d.]+)px, ([-\d.]+)px\)/.exec(transform)!;
  const x = Number(m[1]);
  // 60 + 13 = 73 — an 8px lattice would have clamped this to 72.
  expect(x % 8).not.toBe(0);
});

test('double-click empty canvas opens the create menu; picking adds an unwired node (P1-1)', async ({ page }) => {
  await openCanvas(page);
  await expect(page.locator('.react-flow__edge')).toHaveCount(2);

  await page.locator('.react-flow__pane').dblclick({ position: { x: 700, y: 200 } });
  const menu = page.getByRole('menu', { name: 'Add node' });
  await expect(menu).toBeVisible();
  // Each card's accessible name is its title + one-line description
  // concatenated (DragCreateMenu.tsx) — the "Group" card's description
  // ("Collect media, prompts and loops together") contains "prompt" as a
  // substring, so an unscoped `name: 'Prompt'` (case-insensitive substring
  // match) resolves to both cards (strict-mode violation). Scope to the
  // menuitem whose OWN title span text is the exact string "Prompt".
  await menu
    .getByRole('menuitem')
    .filter({ has: page.getByText('Prompt', { exact: true }) })
    .click();

  await expect(page.locator('.react-flow__node')).toHaveCount(4);
  // No origin — the node arrives unwired.
  await expect(page.locator('.react-flow__edge')).toHaveCount(2);
  // And the double-click did NOT zoom (RF default off when the menu owns it).
  const transform = await page
    .locator('.react-flow__viewport')
    .evaluate((el) => (el as HTMLElement).style.transform);
  expect(transform).toContain('scale(1)');
});

test('right-click empty canvas opens the same create menu (P1-1)', async ({ page }) => {
  await openCanvas(page);
  await page.locator('.react-flow__pane').click({ button: 'right', position: { x: 700, y: 300 } });
  await expect(page.getByRole('menu', { name: 'Add node' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('menu', { name: 'Add node' })).toHaveCount(0);
});
