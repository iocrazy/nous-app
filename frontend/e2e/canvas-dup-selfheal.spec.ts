// e2e/canvas-dup-selfheal.spec.ts
// 2026-08-12 production incident regression — real production build, real
// React Flow, production-SHAPED fixtures (the unit suites use idealized
// string ids; the stubs here reproduce the two wire facts that shipped the
// bug):
//   1. A poisoned canvas row: the numeric-shot-id reconcile regression
//      persisted dozens of copies of each deterministic `shot-{id}` node
//      (102 nodes for 6 shots in the real row). React Flow keeps duplicated
//      ids permanently `visibility:hidden` → the user-reported BLANK canvas
//      (zoom controls only, no nodes).
//   2. `listShots` rows carry bigint ids as JSON *numbers* — at the time of
//      the bug, the shots REST router did not stringify them the way the
//      canvases router does. (#1809, 2026-08-12, closed that specific gap
//      server-side; this fixture keeps the number shape deliberately — see
//      e2e/helpers/realShapes.ts's file header for why it's still a real
//      regression-coverage shape, not stale after #1809.)
// The fixed build must: render the shot nodes VISIBLY (self-heal on load),
// reconcile without re-adding, and persist a healed row with exactly one
// node per shot.

import { expect, test, type Page } from '@playwright/test';
import { setupStubbedSession, TEAM_ID } from './helpers/stubs';
import { realCanvasRow, realSceneRow, realShotRows } from './helpers/realShapes';

const CANVAS_ID = '208443000009999';
const SCENE_ID = 208443000000100; // numeric on the wire, like production
const SHOT_IDS = [
  208443000000001, 208443000000002, 208443000000003,
  208443000000004, 208443000000005, 208443000000006,
];
const COPIES = 17; // 6 × 17 = 102 nodes, mirroring the production row

const poisonedNodes: unknown[] = [];
for (let c = 0; c < COPIES; c++) {
  SHOT_IDS.forEach((sid, i) => {
    poisonedNodes.push({
      id: `shot-${sid}`,
      type: 'shot',
      position: { x: 320, y: i * 400 },
      data: {
        title: '', reference_resource_ids: [], notes: '',
        shot_id: sid, // numeric — exactly as the regression persisted it
        shot_label: `1${String.fromCharCode(65 + i)}`,
        shot_type: 'MEDIUM', camera_angle: 'EYE_LEVEL',
        camera_movement: 'STATIC', focal_length: '35mm',
        description: `Shot ${i + 1}`, image_url: null,
        shot_status: 'empty', gen_task_id: null,
        scene_id: SCENE_ID, // numeric too
      },
    });
  });
}

const CANVAS = realCanvasRow({
  id: CANVAS_ID,
  // Zoomed out far enough that the whole 6-shot column fits the 1280×720
  // e2e viewport — `onlyRenderVisibleElements` culls off-screen nodes, and
  // this test's point is counting VISIBLE ones.
  viewportJson: { x: 0, y: 20, zoom: 0.28 },
  nodesJson: poisonedNodes,
});

const fulfillJson = (body: unknown) => ({
  status: 200,
  contentType: 'application/json',
  body: JSON.stringify(body),
});

async function setupStubs(page: Page, savedPayloads: unknown[]): Promise<void> {
  await page.route(`**/api/v1/canvases/${CANVAS_ID}`, (route) => {
    if (route.request().method() === 'PUT') {
      savedPayloads.push(route.request().postDataJSON());
      return route.fulfill(
        fulfillJson({ success: true, data: { ...CANVAS, base_updated_at: '2020-01-02T00:00:01Z' } }),
      );
    }
    return route.fulfill(fulfillJson({ success: true, data: CANVAS }));
  });
  // Reconcile chain — production wire shapes throughout.
  await page.route('**/api/v1/scripts/projects*', (route) =>
    route.fulfill(
      fulfillJson({
        success: true,
        // fetchScriptProjects unwraps `{ items, total }` — a bare array
        // here silently reads as "no script" and reconcile no-ops.
        data: {
          items: [
            {
              id: '208443000000400',
              episode_id: '208443000000300',
              updated_at: '2026-01-01T00:00:00Z',
            },
          ],
          total: 1,
        },
      }),
    ),
  );
  await page.route('**/api/v1/scripts/208443000000400/scenes', (route) =>
    route.fulfill(
      // id/script_id/chapter_id numeric — toSceneDoc coerces at the
      // frontend boundary, but the wire itself is un-stringified.
      fulfillJson({ success: true, data: [realSceneRow({ id: SCENE_ID })] }),
    ),
  );
  await page.route(`**/api/v1/scenes/${SCENE_ID}/shots`, (route) =>
    route.fulfill(
      // id/scene_id numeric — the shots router's real behaviour.
      fulfillJson({
        success: true,
        data: realShotRows(SHOT_IDS.length, { sceneId: SCENE_ID, baseId: SHOT_IDS[0] }),
      }),
    ),
  );
}

test('poisoned dup-id storyboard row: nodes render VISIBLY, reconcile does not re-add, save heals the row', async ({ page }) => {
  const savedPayloads: Array<{ nodes_json?: Array<{ id: string; data?: { shot_id?: unknown } }> }> = [];
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
  await setupStubs(page, savedPayloads);

  await page.goto(`/team/${TEAM_ID}/canvas/${CANVAS_ID}`);

  // The blank-canvas symptom check: exactly one node per shot, and VISIBLE
  // (the poisoned row rendered 6 nodes permanently `visibility:hidden`
  // before the fix — `.count()` alone would pass on the broken build).
  await expect(page.locator('.react-flow__node')).toHaveCount(6);
  await expect(page.locator('.react-flow__node:visible')).toHaveCount(6);
  await expect(page.locator('.react-flow__node').first()).toContainText('Shot 1');

  // Self-heal persisted: reconcile's type-normalization patches mark the
  // canvas dirty, so a debounced save fires with the healed node set — one
  // node per shot, canonical string shot ids. Polled (rather than reading
  // the first payload) because an initial viewport tick can flush an
  // earlier save before the reconcile patches land.
  const expectedIds = SHOT_IDS.map((sid) => `shot-${sid}`).sort();
  await expect
    .poll(
      () => {
        const last = savedPayloads[savedPayloads.length - 1];
        const nodes = last?.nodes_json ?? [];
        return (
          nodes.length === SHOT_IDS.length &&
          nodes.every((n) => typeof n.data?.shot_id === 'string') &&
          JSON.stringify(nodes.map((n) => n.id).sort()) === JSON.stringify(expectedIds)
        );
      },
      { timeout: 10_000 },
    )
    .toBe(true);
});
