// e2e/canvas-assets.spec.ts
//
// The asset library ON THE CANVAS (P4), end to end through a real browser:
//
//   1. Insert an asset card from the node bar, wire it to a prompt, run — and
//      the dispatched request carries the asset's REFERENCE URLS and its
//      prompt text, not just the words the user typed.
//   2. An output's "As Asset…" opens the library dialog already pointing at the
//      asset the picture came from.
//   3. A pre-P3 `character` card resolves to an asset card ON LOAD, in place.
//   4. …and one the server cannot map says `Unmigrated` instead of guessing.
//
// The component suites cover each piece in isolation; what only a browser can
// show is that the pieces are actually wired to each other — the run path in
// particular spans a bundle fetch, a URL scheme the backend has to be able to
// materialize, and a dispatch body assembled three modules away from the card.
//
// ── Wire shapes ──
// Every fulfilled body is the real one (CLAUDE.md 边界 mock 必须用真实 JSON
// 形状), read off the routers rather than idealized:
//
//   * `/api/v1/assets/*` answers the `{success, data}` Envelope, and EVERY
//     Snowflake on it is a STRING — `_serialize` calls `str()` at the
//     repository boundary, arrays of ids included.
//   * `/api/v1/canvases/{id}` is the odd one out among canvas-adjacent routers
//     in a good way: it already stringifies `id` / `project_id`, which is why
//     `realCanvasRow` shapes them as strings (see e2e/helpers/realShapes.ts).
//   * `GET /api/v1/generated/{id}` answers a `GeneratedItem`
//     (backend/app/schemas/generated.py): string ids, `created_at` as an ISO
//     string, `source` derived server-side, `source_asset_id` nullable.
//   * `GET /assets/resolve-legacy` answers `{"asset_id": <string|null>}`.
//     `null` is a 200 with two documented causes and NOT an error.
//   * The generation dispatch answers `{success, task_ids: [...]}` and the poll
//     answers a task envelope whose `metadata.result_url` is the picture.
//
// The session's team id is the fixtures' Snowflake scope, per
// `setupStubbedSession`'s own docstring: the `/team/{id}` URL segment, the
// `scope_id` query every scoped call carries and the `scope_id` inside the
// bodies then all agree — and the canvas reads its asset scope straight off
// that URL segment (`smart/canvasScope.ts`).

import { expect, test, type Page, type Route } from '@playwright/test';

import { setupStubbedSession } from './helpers/stubs';
import { realCanvasRow } from './helpers/realShapes';

const SCOPE_ID = '727145299382534200';
const PROJECT_ID = '727145299382534000';
const ASSET_ID = '727145299382534300';
const SHEET_FILE = '727145299382534146';
const STILLS_FILE = '727145299382534150';
const GENERATION_ID = '800000000000000001';
const MODEL = 'jimeng-cli-image';

/** 2×2 red PNG — renders in `<img>` with no round trip. */
const PNG =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEUlEQVR4nGP8z8Dwn4EIwAhUBADtgwXqWkAdRgAAAABJRU5ErkJggg==';

// ─── Fixtures ────────────────────────────────────────────────────────────────

function assetFile(resourceId: string, slot: string, sortOrder: number) {
  return {
    asset_id: ASSET_ID,
    resource_id: resourceId,
    slot,
    loadout_id: null,
    sort_order: sortOrder,
    note: null,
    attached_by: null,
    attached_at: '2026-09-01T00:00:00+00:00',
  };
}

/** An `AssetResponse` row — the shelf/search projection. */
const ASSET_SUMMARY = {
  id: ASSET_ID,
  scope_id: SCOPE_ID,
  asset_type: 'character',
  subtype: null,
  name: 'Cole Bannon',
  role_tag: 'lead',
  description: '',
  attrs: {},
  prompt_positive: 'weathered field jacket, grey eyes',
  prompt_negative: 'blurry',
  prompt_positive_zh: null,
  prompt_negative_zh: null,
  platform_params: {},
  cover_file_id: SHEET_FILE,
  source: 'manual',
  duplicated_from: null,
  is_system_preset: false,
  tags: {},
  sort_order: 0,
  created_by: '11111111-1111-1111-1111-111111111111',
  created_at: '2026-09-01T00:00:00+00:00',
  updated_at: '2026-09-01T00:00:00+00:00',
  readiness: { state: 'ready', missing: [] },
  file_counts_by_slot: { sheet: 1, stills: 1 },
  project_ids: [PROJECT_ID],
  loadout_count: 0,
};

/** `AssetDetailResponse` — the summary plus its four relation arrays and
 *  `used_in`, which has a `default_factory` and is therefore never absent. */
const ASSET_DETAIL = {
  ...ASSET_SUMMARY,
  files: [assetFile(SHEET_FILE, 'sheet', 0), assetFile(STILLS_FILE, 'stills', 1)],
  links: [],
  linked_by: [],
  loadouts: [],
  used_in: { canvases: [], storyboards: [] },
};

/** `BundleResponse` for a provider that takes references. `dropped` is empty
 *  here; the node's own suite covers what a trimmed bundle renders. */
const BUNDLE = {
  prompt: { positive: 'weathered field jacket, grey eyes', negative: 'blurry' },
  reference_resource_ids: [SHEET_FILE, STILLS_FILE],
  dropped: [],
  max_refs: 9,
};

const GENERATED_ITEM = {
  id: GENERATION_ID,
  scope_id: SCOPE_ID,
  media_kind: 'image',
  mime: 'image/png',
  prompt: 'weathered field jacket, grey eyes\na lighthouse at dawn',
  model: MODEL,
  provider: 'jimeng-cli',
  origin_kind: 'canvas',
  canvas_id: null,
  node_id: 'out1',
  created_at: '2026-09-02T09:00:00Z',
  promoted_resource_id: null,
  review_state: 'unreviewed',
  // The provenance column P4 Task 7 started writing — what makes the dialog
  // open on THIS character rather than an empty picker.
  source_asset_id: ASSET_ID,
  source: {
    kind: 'canvas',
    label: 'Canvas',
    canvas_id: null,
    node_id: 'out1',
    shot_id: null,
    conversation_id: null,
    deep_link: null,
  },
  title: 'a lighthouse at dawn',
};

const CAPABILITIES = {
  [MODEL]: {
    ratios: ['1:1', '16:9'],
    quality: false,
    resolution: false,
    max_refs: 9,
    negative: true,
    video_modes: [],
  },
};

function promptNode(id: string, position: { x: number; y: number }) {
  return {
    id,
    type: 'prompt',
    position,
    data: {
      body: 'a lighthouse at dawn',
      provider_slug: '',
      agent_id: null,
      run_status: 'idle',
      resource_refs: [],
      gen: { kind: 'image', model: MODEL, ratio: '16:9', count: 1 },
    },
  };
}

/** A pre-P3 character card: bound by `character_id`, a legacy table's row id. */
function legacyCharacterNode(legacyId: string) {
  return {
    id: 'char-legacy-1',
    type: 'character',
    position: { x: 120, y: 120 },
    data: {
      character_id: legacyId,
      name: 'Cole Bannon',
      role_tag: 'lead',
      description: '',
      portrait_url: null,
    },
  };
}

// ─── Routing ─────────────────────────────────────────────────────────────────

interface Recorded {
  /** Every generation dispatch body, in order. */
  dispatches: Record<string, unknown>[];
  /** Every `/assets/{id}/bundle` query, in order. */
  bundleQueries: { assetId: string; model: string | null; loadoutId: string | null }[];
  /** Every `resolve-legacy` query, in order. */
  legacyQueries: { kind: string | null; legacyId: string | null }[];
}

interface RouteOptions {
  canvasId: string;
  nodes: unknown[];
  connections?: unknown[];
  /** What `resolve-legacy` answers. `null` is a real 200. */
  resolveLegacyTo?: string | null;
}

async function routeCanvasApi(
  page: Page,
  rec: Recorded,
  opts: RouteOptions,
): Promise<void> {
  const canvas = realCanvasRow({
    id: opts.canvasId,
    projectId: PROJECT_ID,
    episodeId: null,
    name: 'Asset Board',
    kind: 'smart',
    nodesJson: opts.nodes,
    connectionsJson: opts.connections ?? [],
  });

  // ── /api/v1/assets/* ───────────────────────────────────────────────────
  await page.route(/\/api\/v1\/assets(\/|\?|$)/, (route: Route) => {
    const url = new URL(route.request().url());
    const { pathname } = url;

    // Registered BEFORE `/assets/{id}` for the same reason the router
    // registers the real route first: `resolve-legacy` would otherwise be
    // read as an asset id.
    if (pathname === '/api/v1/assets/resolve-legacy') {
      rec.legacyQueries.push({
        kind: url.searchParams.get('kind'),
        legacyId: url.searchParams.get('legacy_id'),
      });
      return route.fulfill({
        json: { success: true, data: { asset_id: opts.resolveLegacyTo ?? null } },
      });
    }
    if (pathname === '/api/v1/assets') {
      return route.fulfill({ json: { success: true, data: [ASSET_SUMMARY] } });
    }
    const bundle = pathname.match(/^\/api\/v1\/assets\/([^/]+)\/bundle$/);
    if (bundle) {
      rec.bundleQueries.push({
        assetId: bundle[1],
        model: url.searchParams.get('model'),
        loadoutId: url.searchParams.get('loadout_id'),
      });
      return route.fulfill({ json: { success: true, data: BUNDLE } });
    }
    if (pathname === `/api/v1/assets/${ASSET_ID}`) {
      return route.fulfill({ json: { success: true, data: ASSET_DETAIL } });
    }
    return route.fallback();
  });

  // ── /api/v1/generated/{id} — the row behind "As Asset…" ────────────────
  await page.route(`**/api/v1/generated/${GENERATION_ID}*`, (route) =>
    route.fulfill({ json: { success: true, data: GENERATED_ITEM } }),
  );

  // ── /api/v1/canvases/* ─────────────────────────────────────────────────
  await page.route(`**/api/v1/canvases/${opts.canvasId}`, (route) =>
    route.fulfill({ json: { success: true, data: canvas } }),
  );
  await page.route(`**/api/v1/canvases/${opts.canvasId}/generations`, (route) => {
    rec.dispatches.push(route.request().postDataJSON() as Record<string, unknown>);
    return route.fulfill({ json: { success: true, task_ids: ['t1'] } });
  });
  await page.route('**/api/v1/canvases/generations/*', (route) =>
    route.fulfill({
      json: {
        success: true,
        data: {
          phase: 'completed',
          status: 'completed',
          error_msg: null,
          metadata: { result_url: PNG, media_kind: 'image' },
        },
      },
    }),
  );
  await page.route('**/api/v1/canvases/generation-models', (route) =>
    route.fulfill({
      json: {
        success: true,
        data: [
          {
            name: MODEL,
            display_name: 'Jimeng Image',
            type: 'image',
            actual_provider: 'jimeng-cli',
          },
        ],
      },
    }),
  );
  await page.route('**/api/v1/canvases/generation-capabilities', (route) =>
    route.fulfill({ json: { success: true, data: CAPABILITIES } }),
  );

  // The prompt node's @-mention search wants the ResourceSearchResponse
  // shape; the harness's generic `{success,data}` catch-all is the wrong
  // envelope and crashes its render.
  await page.route('**/api/v1/resources/search*', (route) =>
    route.fulfill({
      json: {
        results: [],
        counts: { all: 0, video: 0, image: 0, doc: 0, audio: 0, pdf: 0 },
        next_cursor: null,
      },
    }),
  );
  // Covers and thumbnails — the card, the checklist rows and the picker all
  // point `<img src>` here.
  await page.route('**/api/v1/resources/*/cover', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'image/png',
      body: Buffer.from(PNG.split(',')[1], 'base64'),
    }),
  );
}

function newRecorder(): Recorded {
  return { dispatches: [], bundleQueries: [], legacyQueries: [] };
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    try {
      localStorage.setItem('language', 'en');
    } catch {
      /* localStorage unavailable — nothing we can do */
    }
  });
  await setupStubbedSession(page, { teamId: SCOPE_ID });
});

// ─── 1. Insert → wire → run ──────────────────────────────────────────────────

test('an asset card wired into a prompt puts its references and its prompt into the run', async ({
  page,
}) => {
  const rec = newRecorder();
  const canvasId = '208443000009101';
  await routeCanvasApi(page, rec, { canvasId, nodes: [promptNode('p1', { x: 620, y: 140 })] });

  await page.goto(`/team/${SCOPE_ID}/canvas/${canvasId}`);
  await expect(page.locator('.react-flow__node')).toHaveCount(1);

  // Insert: the node bar's Asset chip opens the library picker, and picking a
  // row fetches that asset's DETAIL before creating the card (the file
  // checklist is seeded from it).
  await page.getByTestId('top-node-chip-asset').click();
  const row = page.getByTestId('asset-picker-row');
  await expect(row).toBeVisible();
  await row.click();

  const card = page.getByTestId('smart-asset-node');
  await expect(card).toBeVisible();
  await expect(card).toHaveAttribute('data-asset-id', ASSET_ID);
  await expect(page.locator('.react-flow__node')).toHaveCount(2);
  // The PRIMARY slot is ticked for us and the rest are not — the card arrives
  // referencing the sheet, and the stills are opt-in.
  await expect(page.getByTestId(`asset-node-file-${SHEET_FILE}`)).toBeChecked();
  const stills = page.getByTestId(`asset-node-file-${STILLS_FILE}`);
  await expect(stills).not.toBeChecked();

  // The chip drops the card at the viewport CENTRE, which puts its checklist
  // under the composer island pinned to the bottom of the screen. Move it into
  // the clear band between the two islands with an ordinary drag rather than
  // forcing the click: the overlap is real, and `force` would assert against a
  // control the user could not have reached
  // (`reference-playwright-unstable-may-be-no-frames` — never degrade to force
  // to get past a genuine hit-test failure).
  const cardNode = page.locator('.react-flow__node').filter({ has: card });
  const dropped = (await cardNode.boundingBox())!;
  await page.mouse.move(dropped.x + dropped.width / 2, dropped.y + 12);
  await page.mouse.down();
  await page.mouse.move(260, 230, { steps: 8 });
  await page.mouse.up();

  // Tick the stills, so the run below proves the CHECKLIST is what decides —
  // an assertion against the seeded selection alone could not tell "the card's
  // ticks are delivered" from "every file the asset owns is delivered".
  await stills.check();
  await expect(stills).toBeChecked();

  // Wire: alt-drag the card onto the prompt. `canConnectSmart` allows
  // asset → prompt and refuses the reverse, so this is the real gesture.
  const promptNodeEl = page.locator('.react-flow__node[data-id="p1"]');
  const assetBox = (await cardNode.boundingBox())!;
  const promptBox = (await promptNodeEl.boundingBox())!;
  await expect(page.locator('.react-flow__edge')).toHaveCount(0);

  //
  // The grab point is the card's HEADER — its body is a `nodrag` checklist, so
  // a grab in the middle would not start a node drag at all. The engine probes
  // with the dragged node's CENTRE, not the pointer, so the drop point is
  // offset by however far the centre sits below the grab: aiming the pointer
  // at the target's centre would land the card's centre a half-card lower and
  // hit nothing.
  const grabOffsetY = 12;
  const centreOffsetY = assetBox.height / 2 - grabOffsetY;
  await page.keyboard.down('Alt');
  await page.mouse.move(assetBox.x + assetBox.width / 2, assetBox.y + grabOffsetY);
  await page.mouse.down();
  await page.mouse.move(
    promptBox.x + promptBox.width / 2,
    promptBox.y + promptBox.height / 2 - centreOffsetY,
    { steps: 12 },
  );
  // Mid-drag the target wears the dashed highlight — asserted before the drop
  // so a failure says "never hit the target" rather than "no edge appeared".
  await expect(page.locator('.react-flow__node.mh-snap-target')).toHaveCount(1);
  await page.mouse.up();
  await page.keyboard.up('Alt');
  await expect(page.locator('.react-flow__edge')).toHaveCount(1);

  // Run.
  await page.getByRole('button', { name: 'Cascade Run', exact: true }).click();
  await expect.poll(() => rec.dispatches.length, { timeout: 15_000 }).toBe(1);

  // The bundle was asked for THIS asset and THIS model — the reference ceiling
  // belongs to the provider, so a bundle fetched without the model name would
  // be a different answer.
  expect(rec.bundleQueries).toHaveLength(1);
  expect(rec.bundleQueries[0].assetId).toBe(ASSET_ID);
  expect(rec.bundleQueries[0].model).toBe(MODEL);

  const body = rec.dispatches[0] as {
    prompt: string;
    model: string;
    params: { source_urls?: string[]; negative?: string; source_asset_id?: string };
  };
  // References ride as `/api/v1/resources/{id}/cover` URLs — the resource
  // bridge (P4 Task 3) is what lets the backend materialize them beside
  // `/api/v1/generated-media/` ones. A canvas that sent anything else would
  // have them dropped server-side.
  expect(body.params.source_urls).toEqual([
    `/api/v1/resources/${SHEET_FILE}/cover`,
    `/api/v1/resources/${STILLS_FILE}/cover`,
  ]);
  // The asset's prompt LEADS the user's text rather than replacing it.
  expect(body.prompt).toBe('weathered field jacket, grey eyes\na lighthouse at dawn');
  expect(body.params.negative).toBe('blurry');
  // Provenance: the run says which asset it came from, so the picture lands
  // back under that character in the inbox and in the sheet's history.
  expect(body.params.source_asset_id).toBe(ASSET_ID);
  // And the retired stamp is not written alongside it.
  expect(body.params).not.toHaveProperty('entity_id');
  expect(body.params).not.toHaveProperty('entity_kind');

  await page.screenshot({ path: 'e2e-artifacts/canvas-assets-run.png', fullPage: true });
});

// ─── 2. Output → As Asset… ───────────────────────────────────────────────────

test('As Asset opens the library dialog on the asset the picture came from', async ({
  page,
}) => {
  const rec = newRecorder();
  const canvasId = '208443000009102';
  const outputNode = {
    id: 'out1',
    type: 'output',
    position: { x: 200, y: 120 },
    data: {
      kind: 'image',
      resource_id: null,
      preview_text: '',
      preview_url: PNG,
      crop_region: null,
      // `id` is the `generated_media` row — the field both import endpoints
      // have always sent and which "As Asset…" resolves the row through.
      images: [{ url: PNG, kind: 'image', name: 'one.png', id: GENERATION_ID }],
    },
  };
  await routeCanvasApi(page, rec, { canvasId, nodes: [outputNode] });

  await page.goto(`/team/${SCOPE_ID}/canvas/${canvasId}`);
  const node = page.locator('.react-flow__node[data-id="out1"]');
  await expect(node).toBeVisible();
  // Selecting pins the floating toolbar.
  await node.click();
  const toolbar = node.getByTestId('output-node-toolbar');
  await expect(toolbar).toBeVisible();

  const asAsset = toolbar.getByRole('button', { name: 'As Asset…' });
  await expect(asAsset).toBeEnabled();
  await asAsset.click();

  const dialog = page.getByTestId('save-as-asset-dialog');
  await expect(dialog).toBeVisible();
  // Prefilled: the row's `source_asset_id` is fetched and pinned at the top of
  // the candidate list, labelled as coming from the canvas. Without it the
  // dialog is an empty picker and "regenerate this character" loses its thread.
  const suggested = dialog.getByTestId('sa-candidate').first();
  await expect(suggested).toBeVisible();
  await expect(suggested).toContainText('Cole Bannon');
  await expect(suggested).toContainText('suggested');

  await page.screenshot({ path: 'e2e-artifacts/canvas-assets-as-asset.png', fullPage: true });
});

// ─── 3. + 4. Legacy cards on load ────────────────────────────────────────────

test('a pre-P3 character card becomes an asset card in place', async ({ page }) => {
  const rec = newRecorder();
  const canvasId = '208443000009103';
  await routeCanvasApi(page, rec, {
    canvasId,
    nodes: [legacyCharacterNode('337610660408111')],
    resolveLegacyTo: ASSET_ID,
  });

  await page.goto(`/team/${SCOPE_ID}/canvas/${canvasId}`);
  const card = page.getByTestId('smart-asset-node');
  await expect(card).toBeVisible({ timeout: 15_000 });
  await expect(card).toHaveAttribute('data-asset-id', ASSET_ID);
  // IN PLACE: same node id, so every edge that pointed at the legacy card
  // still points at the asset card. A delete-and-add would orphan them.
  await expect(page.locator('.react-flow__node[data-id="char-legacy-1"]')).toBeVisible();
  await expect(page.locator('.react-flow__node')).toHaveCount(1);
  expect(rec.legacyQueries).toEqual([
    { kind: 'character', legacyId: '337610660408111' },
  ]);
});

test('a legacy card the server cannot map says Unmigrated rather than guessing', async ({
  page,
}) => {
  const rec = newRecorder();
  const canvasId = '208443000009104';
  await routeCanvasApi(page, rec, {
    canvasId,
    nodes: [legacyCharacterNode('337610660408112')],
    // A real 200 with `asset_id: null` — the project never migrated (or was
    // adopted by a run predating the adoption stamp; a re-run repairs those).
    resolveLegacyTo: null,
  });

  await page.goto(`/team/${SCOPE_ID}/canvas/${canvasId}`);
  await expect(page.getByTestId('legacy-unmigrated-badge')).toBeVisible({ timeout: 15_000 });
  // The card is still the legacy one — matching on name would rewire it to an
  // asset nobody chose.
  await expect(page.getByTestId('smart-asset-node')).toHaveCount(0);
  expect(rec.legacyQueries).toHaveLength(1);
});
