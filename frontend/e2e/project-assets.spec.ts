import { test, expect, type Page, type Route } from '@playwright/test';
import { setupStubbedSession } from './helpers/stubs';

/**
 * The project workspace's asset panel, ACTION by ACTION (P3 Task 5/7).
 *
 * Deliberately disjoint from `projects-workspace.spec.ts`, which already walks
 * the module rail and asserts the panel renders its rows: that spec proves the
 * READ path and the Costumes module's existence, and duplicating it here would
 * buy nothing. What has no coverage above the component level is the four
 * WRITE paths, each of which spans a dialog, a mutation and a refetch:
 *
 *   1. Link From Library — search → the candidate filter → pick → the panel's
 *      linked count actually changes.
 *   2. Import From Script — one call lands BOTH types, so the report has to
 *      say per-type what landed and name every row it refused.
 *   3. + New — create in the scope, then reference from the project, and do
 *      NOT navigate away (the shelf's use of the same dialog does).
 *   4. Unlink — removes the reference, and aims it at the ROW's own scope
 *      rather than the panel's guess.
 *
 * ── Wire shapes ──
 * Every fulfilled body below is the real one (CLAUDE.md 边界 mock 必须用真实
 * JSON 形状), read off `assets_router.py` + `schemas/assets.py` rather than
 * idealized:
 *
 *   * Every route on that router answers the `{success, data}` Envelope —
 *     including the two write endpoints, whose data is `{"linked": true}` /
 *     `{"unlinked": true}`. A bare body would be read as a refusal by
 *     `unwrapEnvelope`.
 *   * `AssetResponse.id` / `scope_id` / `cover_file_id` / `duplicated_from` /
 *     `created_by` / `project_ids[]` are STRINGS — the router stringifies
 *     Snowflake BIGINTs at the response boundary, and a JS number literal for
 *     a 15-digit id is exactly the shape the 2026-08-12 canvas incident
 *     shipped on.
 *   * `ImportedAssetItem.asset_id` is `Optional[str]`, and `action` / `linked`
 *     are reported INDEPENDENTLY (schemas/assets.py says so in as many
 *     words) — a `created` row whose ref write failed carries
 *     `linked: false`, and the fixture below includes exactly that row.
 *   * `tags` is an object of group → values, `file_counts_by_slot` is sparse,
 *     `readiness` / `project_ids` / `loadout_count` are server-derived.
 *   * POST `/assets`, POST `project-refs` and the import endpoint all answer
 *     **201**, not 200.
 *
 * ── Scope ──
 * `setupStubbedSession` is given the Snowflake team id the fixtures carry, per
 * its own docstring: the `/team/{id}` URL segment, the `scope_id` query every
 * scoped call sends, and the `scope_id` inside the response bodies then all
 * agree. That is also what makes the scope assertions below meaningful — the
 * panel resolves its write scope from the project's `team_id`, and a wrong
 * guess is the known gap documented on `ProjectAssetsPanel`.
 */

/** The project's asset scope. Snowflake-shaped and string-typed, like the wire. */
const SCOPE_ID = '727145299382534200';
const PROJECT_ID = '727145299382534000';

const PROJECTS_URL = `/team/${SCOPE_ID}/projects`;

const PROJECT = {
  id: PROJECT_ID,
  name: 'Spring Campaign 2026',
  description: 'Short-form ad series',
  owner_id: 'u1',
  team_id: SCOPE_ID,
  project_type: 'external',
  project_group: null,
  announcement: null,
  is_starred: false,
  color_label: null,
  archived_at: null,
  file_count: 3,
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-07-08T10:00:00Z',
  latest_activity: { kind: 'file', actor: 'HG', at: '2026-07-08T10:00:00Z', stalled: false },
};

/** A full `AssetResponse` row. Overrides are spread last so a test can vary
 *  one field without restating twenty that no assertion reads. */
function assetRow(over: Record<string, unknown>): Record<string, unknown> {
  return {
    id: '727145299382534300',
    scope_id: SCOPE_ID,
    asset_type: 'character',
    subtype: null,
    name: 'CLIENT',
    role_tag: 'lead',
    description: '',
    attrs: {},
    prompt_positive: null,
    prompt_negative: null,
    prompt_positive_zh: null,
    prompt_negative_zh: null,
    platform_params: {},
    cover_file_id: null,
    source: 'manual',
    duplicated_from: null,
    is_system_preset: false,
    tags: {},
    sort_order: 10,
    created_by: '11111111-1111-1111-1111-111111111111',
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
    readiness: { state: 'draft', missing: ['sheet'] },
    file_counts_by_slot: {},
    project_ids: [PROJECT_ID],
    loadout_count: 0,
    ...over,
  };
}

/** Ids as their own constants: they appear inside asserted URL strings, and a
 *  `Record<string, unknown>` field cannot be interpolated into one. */
const CLIENT_ID = '727145299382534300';
const DEV_ID = '727145299382534301';
const NARRATOR_ID = '727145299382534700';

const CLIENT = assetRow({ id: CLIENT_ID, name: 'CLIENT' });
const DEV = assetRow({
  id: DEV_ID,
  name: 'DEV',
  role_tag: 'support',
  readiness: { state: 'ready', missing: [] },
  file_counts_by_slot: { sheet: 1 },
});

/**
 * Mutable per-test backend state. The panel refetches after every mutation
 * rather than patching its own list, so "the count changed" is only a real
 * assertion if the SERVER's answer changed — a fixed fixture would let a panel
 * that silently re-rendered its old rows pass.
 */
interface AssetsState {
  linked: Record<string, unknown>[];
  /** Every write the panel issued, in order, for the scope assertions. */
  writes: { method: string; pathname: string; scopeId: string | null; body: unknown }[];
}

async function routeWorkspaceApi(
  page: Page,
  state: AssetsState,
  extra: {
    /** Rows `GET /assets` (the dialog's search) answers with. */
    searchRows?: Record<string, unknown>[];
    /** Body for the import endpoint's 201. */
    importResult?: unknown;
    /** Row `POST /assets` answers with, and which the next list then holds. */
    created?: Record<string, unknown>;
    /** Overrides for the project row — `team_id: null` makes it PERSONAL. */
    project?: Record<string, unknown>;
  } = {},
): Promise<void> {
  // The two write endpoints and the search live under `/api/v1/assets`; the
  // project-scoped read and the import live under `/api/v1/projects`. Two
  // regexes rather than one glob, for the reason projects-workspace.spec.ts
  // gives: a `**\/api/v1/projects*` glob does not match uniformly across
  // depths.
  await page.route(/\/api\/v1\/assets(\/|\?|$)/, (route: Route) => {
    const url = new URL(route.request().url());
    const { pathname } = url;
    const method = route.request().method();
    const scopeId = url.searchParams.get('scope_id');

    if (method === 'GET' && pathname === '/api/v1/assets') {
      return route.fulfill({ json: { success: true, data: extra.searchRows ?? [] } });
    }

    if (method === 'POST' && pathname === '/api/v1/assets') {
      const body = route.request().postDataJSON() as unknown;
      state.writes.push({ method, pathname, scopeId, body });
      return route.fulfill({ status: 201, json: { success: true, data: extra.created ?? CLIENT } });
    }

    // POST /assets/{id}/project-refs  → 201 {linked:true}
    if (method === 'POST' && pathname.endsWith('/project-refs')) {
      const body = route.request().postDataJSON() as unknown;
      state.writes.push({ method, pathname, scopeId, body });
      if (extra.created) state.linked = [...state.linked, extra.created];
      return route.fulfill({ status: 201, json: { success: true, data: { linked: true } } });
    }

    // DELETE /assets/{id}/project-refs/{project_id} → 200 {unlinked:true}
    const unlink = pathname.match(/^\/api\/v1\/assets\/([^/]+)\/project-refs\/([^/]+)$/);
    if (method === 'DELETE' && unlink) {
      state.writes.push({ method, pathname, scopeId, body: null });
      state.linked = state.linked.filter((row) => row.id !== unlink[1]);
      return route.fulfill({ json: { success: true, data: { unlinked: true } } });
    }

    return route.fallback();
  });

  await page.route(/\/api\/v1\/projects(\/|\?|$)/, (route: Route) => {
    const { pathname } = new URL(route.request().url());

    if (pathname === '/api/v1/projects') {
      return route.fulfill({ json: { data: [{ ...PROJECT, ...(extra.project ?? {}) }] } });
    }
    if (pathname === '/api/v1/projects/stages/catalog') {
      return route.fulfill({ json: { data: [] } });
    }
    if (pathname.endsWith('/assets/import-from-script')) {
      state.writes.push({
        method: route.request().method(),
        pathname,
        scopeId: null,
        body: null,
      });
      return route.fulfill({ status: 201, json: { success: true, data: extra.importResult } });
    }
    if (pathname.endsWith('/assets')) {
      return route.fulfill({ json: { success: true, data: state.linked } });
    }
    if (pathname.endsWith('/episodes/progress')) {
      return route.fulfill({ json: { success: true, data: [] } });
    }
    return route.fallback();
  });
}

/** Open the workspace and land on the Characters panel. */
async function openCharactersPanel(page: Page): Promise<void> {
  await page.goto(`${PROJECTS_URL}/${PROJECT_ID}`);
  await expect(page.getByTestId('workspace-topbar')).toBeVisible({ timeout: 10_000 });
  await page.getByTestId('ws-module-characters').click();
  await expect(page.getByTestId('project-assets-panel')).toBeVisible();
}

test.describe('Project assets panel — the write paths (P3)', () => {
  test.beforeEach(async ({ page }) => {
    await setupStubbedSession(page, { teamId: SCOPE_ID });
    await page.addInitScript(() => {
      try {
        localStorage.setItem('language', 'en');
      } catch {
        /* localStorage unavailable — nothing we can do */
      }
    });
  });

  test('Link From Library: searches the scope, hides what cannot be linked, and the count moves', async ({
    page,
  }) => {
    const state: AssetsState = { linked: [CLIENT], writes: [] };
    // Three rows come back from the scope-wide search. Only ONE is a valid
    // candidate — which is the whole point of the dialog's filter:
    //   CLIENT  → already on this panel (a click would be a 201 no-op)
    //   PRESET  → a system preset, whose null scope makes link_project a 422
    //   DEV     → the only linkable row
    const PRESET = assetRow({
      id: '727145299382534999',
      name: 'Narrator Preset',
      scope_id: null,
      is_system_preset: true,
      project_ids: [],
    });
    await routeWorkspaceApi(page, state, { searchRows: [CLIENT, PRESET, DEV] });
    await openCharactersPanel(page);

    await expect(page.getByTestId('project-assets-count')).toHaveText('1 Linked');
    await expect(page.getByTestId('asset-card')).toHaveCount(1);

    // The trigger and the dialog's body share the `link-from-library` testid,
    // so the dialog is addressed by its search box instead — which is also the
    // thing that has to be there for the flow to continue.
    await page.getByTestId('link-from-library').click();
    const search = page.getByRole('searchbox', { name: 'Search The Library' });
    await expect(search).toBeVisible();
    await search.fill('DEV');

    // Exactly one candidate survives the filter, and it is the linkable one.
    // Visibility, not a bare count — the storyboard incident's duplicate nodes
    // were present in the DOM and permanently hidden (CLAUDE.md 前端上线验收).
    await expect(page.getByTestId('link-candidate')).toHaveCount(1);
    await expect(page.getByTestId('link-candidate')).toBeVisible();
    await expect(page.getByTestId('link-candidate')).toHaveAttribute('data-asset-id', DEV_ID);
    await expect(page.getByTestId('link-candidate')).toContainText('DEV');

    // Picking it writes the ref, and the SERVER's list is what changes.
    state.linked = [CLIENT, DEV];
    await page.getByTestId('link-candidate').click();

    await expect(page.getByTestId('project-assets-count')).toHaveText('2 Linked');
    await expect(page.getByTestId('asset-card')).toHaveCount(2);
    await expect(page.getByTestId('asset-card').nth(1)).toBeVisible();
    await expect(page.getByTestId('asset-card').nth(1)).toContainText('DEV');
    // …and the dialog is gone rather than sitting over a stale result list.
    await expect(page.getByTestId('link-candidate')).toHaveCount(0);

    // The ref was aimed at the ASSET's scope and named THIS project.
    const linkWrite = state.writes.find((w) => w.method === 'POST' && w.pathname.endsWith('/project-refs'));
    expect(linkWrite).toBeTruthy();
    expect(linkWrite?.scopeId).toBe(SCOPE_ID);
    expect(linkWrite?.body).toEqual({ project_id: PROJECT_ID });
    expect(linkWrite?.pathname).toBe(`/api/v1/assets/${DEV_ID}/project-refs`);
  });

  test('Import From Script: per-type chips for what landed, one named line per row it refused', async ({
    page,
  }) => {
    const state: AssetsState = { linked: [CLIENT], writes: [] };
    // One run, both types — that is what makes the per-type breakdown load
    // bearing: a Characters panel that gains one card after a "2 Created"
    // summary otherwise reads as an import that under-delivered.
    //
    // Four rows, deliberately one of each meaningful outcome:
    //   CLIENT  skipped/already_linked → benign, gets NO failure line
    //   DEV     created + linked       → counts toward the Characters chip
    //   Radio…  created + linked       → counts toward the Locations chip
    //   A very… created + NOT linked   → the orthogonal case: the asset row
    //                                    landed, its ref did not. It must NOT
    //                                    count toward a chip (no card is
    //                                    coming) and it MUST get a line.
    const importResult = {
      items: [
        {
          name: 'CLIENT',
          asset_type: 'character',
          action: 'skipped',
          asset_id: CLIENT_ID,
          linked: true,
          code: 'already_linked',
          detail: 'Already referenced by this project',
        },
        {
          name: 'DEV',
          asset_type: 'character',
          action: 'created',
          asset_id: DEV_ID,
          linked: true,
          code: null,
          detail: null,
        },
        {
          name: 'Radio Booth',
          asset_type: 'location',
          action: 'created',
          asset_id: '727145299382534500',
          linked: true,
          code: null,
          detail: null,
        },
        {
          name: 'A Very Long Name',
          asset_type: 'location',
          action: 'created',
          asset_id: '727145299382534501',
          linked: false,
          code: 'name_too_long',
          detail: 'name exceeds 120 characters',
        },
      ],
      created: 3,
      linked: 0,
      skipped: 1,
    };
    await routeWorkspaceApi(page, state, { importResult });
    await openCharactersPanel(page);

    state.linked = [CLIENT, DEV];
    await page.getByTestId('import-from-script').click();

    const report = page.getByTestId('import-report');
    await expect(report).toBeVisible();
    await expect(report).toContainText('Imported — 3 Created, 0 Linked, 1 Skipped');

    // Per-type chips, from the `linked` flag rather than `action`: two
    // Characters (CLIENT re-link + DEV) and one Location. The fourth row —
    // created but unreferenced — is excluded, because no card is coming for it.
    const chips = page.getByTestId('import-by-type').locator('span[data-asset-type]');
    await expect(chips).toHaveCount(2);
    await expect(chips.filter({ hasText: 'Characters' })).toHaveText('Characters 2');
    await expect(chips.filter({ hasText: 'Locations' })).toHaveText('Locations 1');

    // Exactly one failure line: the benign `already_linked` row is not one.
    const failures = page.getByTestId('import-failure');
    await expect(failures).toHaveCount(1);
    await expect(failures).toBeVisible();
    await expect(failures).toHaveAttribute('data-name', 'A Very Long Name');
    await expect(failures).toHaveAttribute('data-asset-type', 'location');
    // Name, the type it belonged to, and the TRANSLATED code — not the raw
    // code and not the server's English `detail`, which is only the fallback.
    await expect(failures).toHaveText('A Very Long Name (Location) — That Name Is Too Long To Store');

    // The run also refetched: DEV is on the panel now.
    await expect(page.getByTestId('project-assets-count')).toHaveText('2 Linked');
    await expect(page.getByTestId('asset-card').nth(1)).toContainText('DEV');
  });

  test('+ New: creates in the scope, references it here, and stays on the panel', async ({
    page,
  }) => {
    const state: AssetsState = { linked: [], writes: [] };
    const created = assetRow({ id: NARRATOR_ID, name: 'NARRATOR', role_tag: '' });
    await routeWorkspaceApi(page, state, { created });
    await openCharactersPanel(page);

    // Empty state first — and it is the EMPTY line, not the error line. The
    // two are separate elements precisely so a failed load cannot tell the
    // user their project has no cast.
    await expect(page.getByTestId('project-assets-empty')).toBeVisible();
    await expect(page.getByTestId('project-assets-error')).toHaveCount(0);

    const urlBefore = page.url();
    await page.getByTestId('new-asset').click();
    await expect(page.getByTestId('new-asset-form')).toBeVisible();
    await page.locator('#new-asset-name').fill('NARRATOR');
    await page.getByRole('button', { name: 'Create' }).click();

    // The row is on the panel, from the refetch rather than a local patch.
    await expect(page.getByTestId('asset-card')).toHaveCount(1);
    await expect(page.getByTestId('asset-card')).toBeVisible();
    await expect(page.getByTestId('asset-card')).toContainText('NARRATOR');
    await expect(page.getByTestId('project-assets-count')).toHaveText('1 Linked');
    await expect(page.getByTestId('new-asset-form')).toHaveCount(0);

    // NO navigation. The shelf's use of this same dialog opens the new asset's
    // sheet; here the user is building a cast and expects to keep adding.
    expect(page.url()).toBe(urlBefore);
    await expect(page.getByTestId('project-assets-panel')).toBeVisible();

    // Two writes, in order: create into the scope, then reference from here.
    expect(state.writes.map((w) => `${w.method} ${w.pathname}`)).toEqual([
      'POST /api/v1/assets',
      `POST /api/v1/assets/${NARRATOR_ID}/project-refs`,
    ]);
    expect(state.writes[0].scopeId).toBe(SCOPE_ID);
    expect(state.writes[0].body).toMatchObject({ asset_type: 'character', name: 'NARRATOR' });
    expect(state.writes[1].scopeId).toBe(SCOPE_ID);
    expect(state.writes[1].body).toEqual({ project_id: PROJECT_ID });
  });

  test('Unlink: works even when the panel has no write scope, because it uses the row’s', async ({
    page,
  }) => {
    // This is the one place `asset.scope_id ?? scopeId` earns its keep, and it
    // is reachable rather than hypothetical.
    //
    // The panel guesses its write scope as `project.team_id ?? personalTeamId`.
    // On a PERSONAL project (`team_id: null`) whose viewer has no resolved
    // personal team yet, that guess is NULL — so `Link From Library` and
    // `+ New` are disabled and say why. The READ still works: that route takes
    // no `scope_id` at all, deriving it server-side from the project's owner.
    //
    // Unlink is deliberately NOT gated on the guess (its button is disabled
    // only while another unlink is in flight) and goes through anyway, because
    // it sends the scope the SERVER just said the row lives in. Collapsing it
    // to the guess would leave the user looking at rows they cannot remove.
    //
    // What this deliberately does NOT claim: that a row's scope and a
    // PRESENT guess ever disagree. `link_project` refuses any asset whose
    // scope differs from the project's resolved scope (422
    // `project_scope_mismatch`), so on a consistent database the two agree
    // wherever a write can succeed at all — and where they would differ (a
    // collaborator on someone else's personal project) the scope gate answers
    // 403 `not_a_member` before any of this is reached. The reachable gap is
    // the guess being ABSENT, not being different.
    const OWNER_SCOPE_ID = '727145299382534900';
    const owned = (over: Record<string, unknown>) =>
      assetRow({ ...over, scope_id: OWNER_SCOPE_ID });
    const state: AssetsState = {
      linked: [owned({ id: CLIENT_ID, name: 'CLIENT' }), owned({ id: DEV_ID, name: 'DEV' })],
      writes: [],
    };

    // Leave the personal team unresolved. Two halves, because either one alone
    // gets overwritten: drop the key `setupStubbedSession` seeded (this init
    // script runs after its), and answer `team_members` with nothing — which
    // is `fetchPersonalTeam`'s own early-return-null branch, so it never sets
    // the state or writes the key back.
    await page.addInitScript(() => {
      try {
        localStorage.removeItem('mediahub_personal_team');
      } catch {
        /* localStorage unavailable — nothing we can do */
      }
    });
    await page.route('**/rest/v1/team_members*', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }),
    );

    await routeWorkspaceApi(page, state, { project: { team_id: null } });
    await openCharactersPanel(page);

    // The read landed even though there is no scope to write with.
    await expect(page.getByTestId('project-assets-count')).toHaveText('2 Linked');

    // The two scope-dependent actions are off and say why, rather than failing
    // on click. This also proves the guess really is absent — without it the
    // test would pass on a build that resolved a scope after all, and the
    // assertion below would be back to comparing two equal values.
    await expect(page.getByTestId('link-from-library')).toBeDisabled();
    await expect(page.getByTestId('new-asset')).toBeDisabled();
    await expect(page.getByTestId('link-from-library')).toHaveAttribute(
      'title',
      'Workspace Not Resolved Yet',
    );

    // Unlink is not gated on it, and goes through.
    const unlink = page.locator(`[data-testid="unlink-asset"][data-asset-id="${CLIENT_ID}"]`);
    await expect(unlink).toBeEnabled();
    await unlink.click();

    await expect(page.getByTestId('project-assets-count')).toHaveText('1 Linked');
    await expect(page.getByTestId('asset-card')).toHaveCount(1);
    await expect(page.getByTestId('asset-card')).toBeVisible();
    await expect(page.getByTestId('asset-card')).toContainText('DEV');

    // One DELETE, both ids in the path, and `scope_id` is the ROW's — the only
    // value available here, since the panel's own is null.
    expect(state.writes).toHaveLength(1);
    expect(state.writes[0].method).toBe('DELETE');
    expect(state.writes[0].pathname).toBe(
      `/api/v1/assets/${CLIENT_ID}/project-refs/${PROJECT_ID}`,
    );
    expect(state.writes[0].scopeId).toBe(OWNER_SCOPE_ID);
  });
});
