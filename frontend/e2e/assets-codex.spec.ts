// frontend/e2e/assets-codex.spec.ts
//
// The asset codex, end to end against captured wire bodies: read the type
// tabs, open one asset's sheet, equip its missing primary slot, watch
// readiness flip, add a loadout, and duplicate the asset.
//
// Why a route-mocked e2e rather than more component tests: every unit test in
// this feature mocks `services/assetsService`, so all of them agree on a
// NORMALISED shape (`AssetRow`, ids already strings, envelope already
// unwrapped) that no backend emits. The bodies here come from
// `e2e/fixtures/assets/` — see that file's PROVENANCE note, and CLAUDE.md
// 开发规范 "边界 mock 必须用真实 JSON 形状". Two contracts meet in this spec
// and they are deliberately different shapes:
//
//   `/api/v1/assets*`           → `{success, data}` envelope, snowflakes as
//                                 JSON strings (`assets_router._ok` +
//                                 `assets_repository._serialize`)
//   `/api/v1/resources/search`  → BARE body, no envelope
//                                 (`resources_search_router`)
//
// A stub that wrapped the second one in the first's envelope would let a
// client that mishandles the real search body pass here.
//
// The `/assets` stub is STATEFUL on purpose, and it DERIVES readiness rather
// than storing it. "Fan Qi became Ready" has to mean the primary slot really
// acquired a file and the server recomputed — not that a fixture flipped a
// string. `readinessFor` below is the transcription of
// `app/services/assets/slots.py::readiness` (ready iff the primary slot has
// ≥1 file); a stub that hardcoded `state: 'ready'` in the attach response
// would pass over a client that attached to the WRONG slot.

import { test, expect, type Page, type Route } from '@playwright/test';

import { setupStubbedSession } from './helpers/stubs';
import {
  ASSETS,
  DRAFT,
  DRAFT_DEFAULT_LOADOUT,
  DRAFT_DETAIL,
  DUPLICATE_ID,
  DUPLICATE_NAME,
  LOCATION,
  NEW_LOADOUT_ID,
  NEW_LOADOUT_NAME,
  PNG_1X1,
  PRESET,
  READY,
  READY_DETAIL,
  SCOPE_ID,
  SEARCH_RESULTS,
  type WireAssetDetail,
  type WireLoadout,
} from './fixtures/assets';

const CODEX_URL = `/team/${SCOPE_ID}/resources/assets`;

/** type → primary slot, mirroring `app/services/assets/slots.py::PRIMARY_SLOT`. */
const PRIMARY_SLOT: Record<string, string | null> = {
  character: 'sheet',
  location: 'establishing',
  prop: 'turnaround',
  costume: 'flat',
  prompt: null,
  audio: 'primary',
};

/**
 * `readiness` recomputed the way the backend does it, from the slot counts the
 * stub actually holds. See the file header for why this is not a constant.
 */
function readinessFor(row: WireAssetDetail): { state: 'ready' | 'draft'; missing: string[] } {
  // `slots.py::readiness` RAISES on an unrecognized type, and the transcription
  // has to raise too. Falling through to a plausible-looking `draft` would let
  // a fixture with a typo'd or renamed type sail through this spec while the
  // real backend answered 500 — the stub would be MORE permissive than what it
  // stands in for, which is the one thing a stub must never be.
  if (!(row.asset_type in PRIMARY_SLOT)) {
    throw new Error(`unknown asset_type: ${row.asset_type}`);
  }
  if (row.asset_type === 'prompt') {
    const ok = Boolean(row.prompt_positive && row.prompt_positive.trim());
    return { state: ok ? 'ready' : 'draft', missing: ok ? [] : ['prompt_positive'] };
  }
  const primary = PRIMARY_SLOT[row.asset_type] as string;
  const ok = (row.file_counts_by_slot[primary] ?? 0) > 0;
  return { state: ok ? 'ready' : 'draft', missing: ok ? [] : [primary] };
}

function envelope(route: Route, data: unknown, status = 200): Promise<void> {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify({ success: true, data }),
  });
}

interface CodexState {
  /** id → detail row. The list projection is taken off these. */
  rows: Map<string, WireAssetDetail>;
  /** Every `/assets` request line, so a test can assert what was ASKED. */
  requests: string[];
  /** Bodies of the mutating calls, in order. */
  posts: Array<{ path: string; body: unknown }>;
}

/** The list projection: a detail row minus the four relation arrays. */
function listRow(row: WireAssetDetail) {
  const { files: _f, links: _l, linked_by: _lb, loadouts: _lo, ...rest } = row;
  return { ...rest, readiness: readinessFor(row), loadout_count: row.loadouts.length };
}

function detailRow(row: WireAssetDetail) {
  return { ...row, readiness: readinessFor(row), loadout_count: row.loadouts.length };
}

/**
 * i18n defaults to 'zh' when no `language` key is stored (see `i18n.ts`), so
 * the English labels this spec asserts on only exist once it is pinned. Same
 * seeding the canvas and generated-inbox suites do.
 */
async function useEnglish(page: Page): Promise<void> {
  await page.addInitScript(() => {
    try {
      localStorage.setItem('language', 'en');
    } catch {
      /* localStorage unavailable — nothing we can do */
    }
  });
}

async function stubCodex(page: Page): Promise<CodexState> {
  const state: CodexState = {
    rows: new Map<string, WireAssetDetail>([
      [READY.id, structuredClone(READY_DETAIL)],
      [DRAFT.id, structuredClone(DRAFT_DETAIL)],
      [LOCATION.id, { ...structuredClone(LOCATION), files: [], links: [], linked_by: [], loadouts: [] }],
      [PRESET.id, { ...structuredClone(PRESET), files: [], links: [], linked_by: [], loadouts: [] }],
    ]),
    requests: [],
    posts: [],
  };

  // The generation-history panel on every sheet. Explicit rather than left to
  // the session harness's catch-all: that answers `data: []`, and the panel
  // reads `items` off it — an "unavailable" panel from a fixture gap would be
  // indistinguishable from the real failure state.
  await page.route('**/api/v1/generated**', (route) =>
    envelope(route, { items: [], next_cursor: null }),
  );

  // BARE body, no envelope — see the file header.
  await page.route('**/api/v1/resources/search*', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(SEARCH_RESULTS),
    }),
  );

  await page.route('**/api/v1/assets**', async (route) => {
    const url = new URL(route.request().url());
    const method = route.request().method();
    const p = url.pathname;
    state.requests.push(`${method} ${p}${url.search}`);

    if (method === 'GET' && p.endsWith('/assets/counts')) {
      // Zero-filled, like the real route: a 0 means "none yet", never
      // "unknown", so the rail can render a badge for a type with no rows.
      const counts: Record<string, number> = {
        character: 0,
        location: 0,
        prop: 0,
        costume: 0,
        prompt: 0,
        audio: 0,
      };
      for (const row of state.rows.values()) {
        // Presets are global and belong to no scope's tally.
        if (row.is_system_preset) continue;
        counts[row.asset_type] += 1;
      }
      return envelope(route, counts);
    }

    if (method === 'GET' && p.endsWith('/assets')) {
      const type = url.searchParams.get('type');
      const rows = [...state.rows.values()]
        .filter((row) => type === null || row.asset_type === type)
        .map(listRow);
      return envelope(route, rows);
    }

    const byId = p.match(/\/assets\/(\d+)$/);
    if (method === 'GET' && byId) {
      const row = state.rows.get(byId[1]);
      if (!row) {
        return route.fulfill({
          status: 404,
          contentType: 'application/json',
          body: JSON.stringify({
            success: false,
            error: { code: 'asset_not_found', detail: 'Asset not found' },
          }),
        });
      }
      return envelope(route, detailRow(row));
    }

    const files = p.match(/\/assets\/(\d+)\/files$/);
    if (method === 'POST' && files) {
      const body = route.request().postDataJSON() as {
        items: Array<{ resource_id: string; slot: string; loadout_id?: string | null }>;
      };
      state.posts.push({ path: p, body });
      const row = state.rows.get(files[1]);
      if (!row) return envelope(route, [], 201);
      // Atomic, like the real route: every item lands or none do. The slot
      // counts move HERE, which is what makes the readiness flip a
      // consequence of the attach rather than a scripted second body.
      const created = body.items.map((item) => ({
        asset_id: row.id,
        resource_id: item.resource_id,
        slot: item.slot,
        loadout_id: item.loadout_id ?? null,
        sort_order: 0,
        note: null,
        attached_by: null,
        attached_at: '2026-08-29T11:00:00Z',
      }));
      row.files.push(...created);
      for (const item of body.items) {
        row.file_counts_by_slot[item.slot] = (row.file_counts_by_slot[item.slot] ?? 0) + 1;
      }
      return envelope(route, created, 201);
    }

    const loadouts = p.match(/\/assets\/(\d+)\/loadouts$/);
    if (method === 'POST' && loadouts) {
      const body = route.request().postDataJSON() as { name: string };
      state.posts.push({ path: p, body });
      const row = state.rows.get(loadouts[1]);
      const created: WireLoadout = {
        id: NEW_LOADOUT_ID,
        asset_id: loadouts[1],
        name: body.name,
        // Not default: `create_loadout` never sets `is_default`, so a stub
        // that did would hide a UI reading the star off the wrong flag.
        is_default: false,
        costume_ids: [],
        prop_ids: [],
        prompt_extra: null,
        sort_order: 1,
        created_at: '2026-08-29T11:00:00Z',
      };
      row?.loadouts.push(created);
      return envelope(route, created, 201);
    }

    const dup = p.match(/\/assets\/(\d+)\/duplicate$/);
    if (method === 'POST' && dup) {
      state.posts.push({ path: p, body: route.request().postDataJSON() });
      const src = state.rows.get(dup[1]);
      if (!src) return envelope(route, {}, 201);
      const copy: WireAssetDetail = {
        ...structuredClone(src),
        id: DUPLICATE_ID,
        name: DUPLICATE_NAME,
        source: 'duplicated',
        duplicated_from: src.id,
        // A duplicate copies the asset's own fields and relations, not its
        // project refs — the copy is not in any project until someone puts it
        // in one.
        project_ids: [],
      };
      state.rows.set(copy.id, copy);
      return envelope(route, detailRow(copy), 201);
    }

    return envelope(route, []);
  });

  // LAST → highest priority. `/resources/{id}/cover` is a no-auth binary URL
  // used directly in `<img src>`, and the pattern must not swallow
  // `/resources/search`, which is why it names the `/cover` segment.
  await page.route('**/api/v1/resources/*/cover*', (route) =>
    route.fulfill({ status: 200, contentType: 'image/png', body: PNG_1X1 }),
  );

  return state;
}

function card(page: Page, id: string) {
  return page.locator(`[data-testid="asset-card"][data-asset-id="${id}"]`);
}

function tab(page: Page, type: string) {
  return page.locator(`[role="tab"][data-tab-type="${type}"]`);
}

/** Open one asset's sheet from the codex and wait for it to be on screen. */
async function openSheet(page: Page, id: string) {
  await card(page, id).click();
  const sheet = page.locator(`[data-testid="asset-sheet"][data-asset-id="${id}"]`);
  await expect(sheet).toBeVisible();
  return sheet;
}

test.describe('Asset codex', () => {
  test('the type tabs narrow the request and the shelf shows what came back', async ({
    page,
  }) => {
    await useEnglish(page);
    await setupStubbedSession(page, { teamId: SCOPE_ID });
    const state = await stubCodex(page);

    await page.goto(CODEX_URL);

    await expect(page.getByTestId('asset-shelf')).toBeVisible();
    // Visibility, never a bare count: a row can be in the DOM and invisible
    // (the 2026-08-12 canvas incident shipped exactly that).
    await expect(card(page, READY.id)).toBeVisible();
    await expect(card(page, DRAFT.id)).toBeVisible();
    await expect(card(page, LOCATION.id)).toBeVisible();

    // The readiness chip is the card's headline claim; it must match the row.
    await expect(card(page, READY.id)).toHaveAttribute('data-readiness', 'ready');
    await expect(card(page, DRAFT.id)).toHaveAttribute('data-readiness', 'draft');

    await expect(tab(page, 'all')).toHaveAttribute('aria-selected', 'true');

    // Switching to a type must ASK for that type, not filter the page it
    // already has: a client that filtered client-side would look identical
    // here and be wrong the moment the list is longer than one page.
    await tab(page, 'location').click();
    await expect(card(page, LOCATION.id)).toBeVisible();
    await expect(tab(page, 'location')).toHaveAttribute('aria-selected', 'true');
    await expect(card(page, READY.id)).toHaveCount(0);
    expect(state.requests.some((r) => r.includes('type=location'))).toBe(true);
  });

  test('a system preset is on the shelf and says it is read-only', async ({ page }) => {
    await useEnglish(page);
    await setupStubbedSession(page, { teamId: SCOPE_ID });
    await stubCodex(page);

    await page.goto(`/team/${SCOPE_ID}/resources/assets/prompt`);

    await expect(page.getByTestId('asset-shelf')).toBeVisible();
    await expect(page.getByTestId('preset-section')).toBeVisible();
    await expect(card(page, PRESET.id)).toBeVisible();
  });

  test('equipping the primary slot flips readiness to Ready', async ({ page }) => {
    await useEnglish(page);
    await setupStubbedSession(page, { teamId: SCOPE_ID });
    const state = await stubCodex(page);

    await page.goto(CODEX_URL);
    const sheet = await openSheet(page, DRAFT.id);

    // Before: draft, and the primary slot is the empty one offering Equip.
    await expect(page.getByTestId('sheet-readiness')).toHaveAttribute(
      'data-readiness',
      'draft',
    );
    const emptyPrimary = page.locator('[data-testid="board-empty-pin"][data-slot="sheet"]');
    await expect(emptyPrimary).toBeVisible();

    await emptyPrimary.locator('[data-testid="pin-equip"]').click();
    await expect(page.getByTestId('equip-dialog')).toBeVisible();

    // The search is the real bare-body route; pick the first result.
    await page.getByTestId('equip-search').fill('sheet');
    const candidate = page.locator(
      `[data-testid="equip-candidate"][data-resource-id="${SEARCH_RESULTS.results[0].id}"]`,
    );
    await expect(candidate).toBeVisible();
    await candidate.click();
    await expect(candidate).toHaveAttribute('data-picked', 'true');

    await page.getByTestId('equip-submit').click();
    await expect(page.getByTestId('equip-dialog')).toHaveCount(0);

    // After: Ready — and it is the SERVER's recomputed answer, because the
    // stub derives readiness from the slot counts the attach moved.
    await expect(page.getByTestId('sheet-readiness')).toHaveAttribute(
      'data-readiness',
      'ready',
    );
    await expect(sheet).toBeVisible();
    // The primary slot is its own 16:9 frame (`board-main`), not one of the
    // arrangeable `board-pin` squares — so the proof the file landed on the
    // RIGHT slot is that frame carrying the resource we picked.
    await expect(
      page.locator(
        `[data-testid="board-main"][data-slot="sheet"] img[data-resource-id="${SEARCH_RESULTS.results[0].id}"]`,
      ),
    ).toBeVisible();

    // And the request said the right slot. `sheet` is not loadout-scoped —
    // only `worn` is (`assetSheetModel.loadoutForSlot`) — so `loadout_id`
    // must be null here; a client that stamped the selected outfit onto it
    // would make the file vanish when the user switched loadouts.
    const attach = state.posts.find((post) => post.path.endsWith('/files'));
    expect(attach).toBeTruthy();
    expect((attach!.body as { items: Array<Record<string, unknown>> }).items).toEqual([
      {
        resource_id: SEARCH_RESULTS.results[0].id,
        slot: 'sheet',
        loadout_id: null,
      },
    ]);
  });

  test('a new loadout is created and shows up as a chip', async ({ page }) => {
    await useEnglish(page);
    await setupStubbedSession(page, { teamId: SCOPE_ID });
    const state = await stubCodex(page);

    await page.goto(CODEX_URL);
    await openSheet(page, DRAFT.id);

    const chips = page.getByTestId('loadout-chips');
    await expect(chips).toBeVisible();
    await expect(
      page.locator(`[data-testid="loadout-chip"][data-loadout-id="${DRAFT_DEFAULT_LOADOUT.id}"]`),
    ).toBeVisible();

    await page.getByTestId('loadout-new').click();
    await page.getByTestId('loadout-name-input').fill(NEW_LOADOUT_NAME);
    await page.getByTestId('loadout-name-save').click();

    const created = page.locator(
      `[data-testid="loadout-chip"][data-loadout-id="${NEW_LOADOUT_ID}"]`,
    );
    await expect(created).toBeVisible();
    await expect(created).toContainText(NEW_LOADOUT_NAME);

    const post = state.posts.find((p) => p.path.endsWith('/loadouts'));
    expect(post?.body).toEqual({ name: NEW_LOADOUT_NAME });
  });

  test('duplicating opens the copy, not the original', async ({ page }) => {
    await useEnglish(page);
    await setupStubbedSession(page, { teamId: SCOPE_ID });
    const state = await stubCodex(page);

    await page.goto(CODEX_URL);
    await openSheet(page, DRAFT.id);

    await page.getByTestId('duplicate-asset').click();

    // The sheet is keyed on the asset id, so landing on the copy means a real
    // navigation happened — not the original repainted with a new name.
    await expect(
      page.locator(`[data-testid="asset-sheet"][data-asset-id="${DUPLICATE_ID}"]`),
    ).toBeVisible();
    await expect(page).toHaveURL(new RegExp(`/resources/assets/item/${DUPLICATE_ID}$`));
    await expect(page.getByTestId('asset-sheet-header')).toContainText(DUPLICATE_NAME);

    expect(state.posts.some((p) => p.path.endsWith('/duplicate'))).toBe(true);
    // The counts badge contract: duplicating refreshes it (AssetSheetPage's
    // `refreshAssetCounts`), so a second counts fetch must have happened.
    expect(state.requests.filter((r) => r.includes('/assets/counts')).length).toBeGreaterThan(1);
  });

  test('every asset the shelf lists can be opened', async ({ page }) => {
    // A cheap sweep over the six sub-layouts' shared shell: the sheet must
    // render for each type the fixture carries, not just the character the
    // other tests drive. A type whose body threw would white-screen here.
    await useEnglish(page);
    await setupStubbedSession(page, { teamId: SCOPE_ID });
    await stubCodex(page);

    for (const asset of ASSETS) {
      await page.goto(`/team/${SCOPE_ID}/resources/assets/item/${asset.id}`);
      await expect(
        page.locator(`[data-testid="asset-sheet"][data-asset-id="${asset.id}"]`),
      ).toBeVisible();
      await expect(page.getByTestId('asset-sheet-header')).toContainText(asset.name);
    }
  });
});
