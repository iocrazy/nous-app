// frontend/e2e/generated-inbox.spec.ts
//
// The Generated inbox, end to end against captured wire bodies: open the view,
// read the tab counters, turn one generation into part of an asset, and run
// the clean-up preview → delete gate.
//
// Why a route-mocked e2e rather than more component tests: every unit test in
// this feature mocks the SERVICE layer, so all of them agree on a normalised
// shape that no backend produces. The bodies here come from
// `e2e/fixtures/generated/` — captured off the real response models, with the
// snowflakes as strings and `created_at` as an ISO string (see that file's
// PROVENANCE note, and CLAUDE.md 开发规范 "边界 mock 必须用真实 JSON 形状").
//
// The `/generated` stub is STATEFUL on purpose. "The card left Unreviewed"
// has to mean the server stopped returning it there, not just that the client
// repainted a pill: the in-place patch after a successful attach and the next
// fetch of the tab are two different claims, and only a stub that actually
// moves the row can tell them apart.

import { test, expect, type Page, type Route } from '@playwright/test';

import { setupStubbedSession } from './helpers/stubs';
import {
  ASSET_LIST,
  CLEANUP_DRY,
  COUNTS,
  IN_ASSETS,
  INTERMEDIATE,
  ITEMS,
  PNG_1X1,
  SAVED,
  SAVE_AS_ASSET_201,
  SCOPE_ID,
  SUGGESTED_ASSET_DETAIL,
  SUGGESTED_ASSET_ID,
  UNREVIEWED,
  type WireGeneratedItem,
} from './fixtures/generated';

const INBOX_URL = `/team/${SCOPE_ID}/resources/generated`;

function envelope(route: Route, data: unknown, status = 200): Promise<void> {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify({ success: true, data }),
  });
}

interface InboxState {
  /** id → row, in the order the list returns them (newest first). */
  rows: Map<string, WireGeneratedItem>;
  /** Every `/generated` request line, so a test can assert what was asked. */
  requests: string[];
}

/**
 * Intercept `/api/v1/generated*`, `/api/v1/assets*` and the cover stream.
 *
 * Registration order is load-bearing: Playwright checks handlers in REVERSE
 * registration order, and `**\/api/v1/generated**` also matches
 * `/api/v1/generated-media/...`. The cover route is therefore registered
 * LAST so it wins for the media paths.
 */
/**
 * i18n defaults to 'zh' when no `language` key is stored (see `i18n.ts`), so
 * the English labels this spec asserts on only exist once it is pinned. Same
 * seeding the canvas suites do.
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

async function stubInbox(page: Page): Promise<InboxState> {
  const state: InboxState = {
    rows: new Map(
      [...ITEMS, INTERMEDIATE].map((row) => [row.id, { ...row }]),
    ),
    requests: [],
  };

  await page.route('**/api/v1/generated**', async (route) => {
    const url = new URL(route.request().url());
    const method = route.request().method();
    const p = url.pathname;
    state.requests.push(`${method} ${p}${url.search}`);

    if (method === 'GET' && p.endsWith('/generated/counts')) {
      // The counters are the backend's, not a count of what this page holds:
      // a UI that derived them from `rows` could never show the real
      // "12 unreviewed" against 1 loaded card, and would hide a bug where
      // the view counts its own list instead of asking.
      return envelope(route, COUNTS);
    }

    if (method === 'GET' && p.endsWith('/generated')) {
      const state_ = url.searchParams.get('state') ?? 'unreviewed';
      // The SERVER decides whether intermediates are in the page — the
      // response body carries no marker a client could filter on
      // (`generated_media.params` is not on the wire). A stub that always
      // returned the mask would let a client that never sends the flag pass.
      const withIntermediates = url.searchParams.get('include_intermediate') === 'true';
      const items = [...state.rows.values()].filter(
        (row) =>
          (state_ === 'all' || row.review_state === state_) &&
          (withIntermediates || row.id !== INTERMEDIATE.id),
      );
      return envelope(route, { items, next_cursor: null });
    }

    const saveAsAsset = p.match(/\/generated\/(\d+)\/save-as-asset$/);
    if (method === 'POST' && saveAsAsset) {
      const row = state.rows.get(saveAsAsset[1]);
      if (row) {
        row.review_state = 'in_assets';
        row.promoted_resource_id = SAVE_AS_ASSET_201.resource_id;
        row.source_asset_id = SAVE_AS_ASSET_201.asset_id;
      }
      return envelope(
        route,
        { ...SAVE_AS_ASSET_201, generation: row ?? SAVE_AS_ASSET_201.generation },
        201,
      );
    }

    if (method === 'POST' && p.endsWith('/generated/cleanup')) {
      const body = route.request().postDataJSON() as { dry_run?: boolean };
      // The schema's default is a dry run, so an absent flag is a PREVIEW.
      // Reading `undefined` as "go ahead and delete" here would let the spec
      // pass over a client that forgot to send it.
      if (body?.dry_run !== false) return envelope(route, CLEANUP_DRY);
      for (const row of CLEANUP_DRY.sample) state.rows.delete(row.id);
      return envelope(route, {
        dry_run: false,
        count: CLEANUP_DRY.count,
        sample: [],
        deleted: CLEANUP_DRY.count,
        truncated: false,
      });
    }

    return envelope(route, { items: [], next_cursor: null });
  });

  await page.route('**/api/v1/assets**', async (route) => {
    const url = new URL(route.request().url());
    const byId = url.pathname.match(/\/assets\/(\d+)$/);
    if (byId) {
      return envelope(
        route,
        byId[1] === SUGGESTED_ASSET_ID ? SUGGESTED_ASSET_DETAIL : ASSET_LIST[0],
      );
    }
    if (route.request().method() === 'GET') {
      const type = url.searchParams.get('type');
      // The dialog asks per type; answering every type with the character
      // list would hide a picker that forgot to pass one.
      return envelope(route, type === 'character' ? ASSET_LIST : []);
    }
    return envelope(route, ASSET_LIST[0], 201);
  });

  // LAST → highest priority. `/generated-media/{id}/cover` and `/stream` are
  // no-auth binary URLs used directly in `<img src>` / `<video poster>`.
  await page.route('**/api/v1/generated-media/**', (route) =>
    route.fulfill({ status: 200, contentType: 'image/png', body: PNG_1X1 }),
  );

  return state;
}

function card(page: Page, id: string) {
  return page.locator(`[data-testid="generated-card"][data-generation-id="${id}"]`);
}

/**
 * The clean-up dialog's Preview button.
 *
 * `exact` is not optional decoration: every card's thumbnail is now a button
 * named "Preview <title>", and Playwright's `name` matches as a SUBSTRING, so
 * the bare form resolves to every card plus this one and fails strict mode.
 * (Same trap as the douyin publish selectors — see CLAUDE.md.)
 */
function previewButton(page: Page) {
  return page.getByRole('button', { name: 'Preview', exact: true });
}

function tab(page: Page, name: string) {
  return page.getByRole('tab', { name: new RegExp(`^${name}`) });
}

test.describe('Generated inbox', () => {
  test('tabs carry the backend counters and the unreviewed card is on screen', async ({
    page,
  }) => {
    await useEnglish(page);
    await setupStubbedSession(page, { teamId: SCOPE_ID });
    const state = await stubInbox(page);

    await page.goto(INBOX_URL);

    await expect(page.getByTestId('generated-view')).toBeVisible();
    // Visibility, never a bare count: a row can be in the DOM and invisible
    // (the 2026-08-12 canvas incident shipped exactly that).
    await expect(card(page, UNREVIEWED.id)).toBeVisible();
    await expect(page.getByText(UNREVIEWED.title, { exact: true })).toBeVisible();

    await expect(tab(page, 'Unreviewed')).toContainText(String(COUNTS.unreviewed));
    await expect(tab(page, 'Saved')).toContainText(String(COUNTS.saved));
    await expect(tab(page, 'In Assets')).toContainText(String(COUNTS.in_assets));

    // Default tab is unreviewed, and the other two rows are NOT rendered into it.
    await expect(card(page, SAVED.id)).toHaveCount(0);
    await expect(card(page, IN_ASSETS.id)).toHaveCount(0);
    expect(state.requests.some((r) => r.startsWith('GET /api/v1/generated?'))).toBe(true);
  });

  test('As Asset attaches to the suggested asset and the row moves to In Assets', async ({
    page,
  }) => {
    await useEnglish(page);
    await setupStubbedSession(page, { teamId: SCOPE_ID });
    const state = await stubInbox(page);

    await page.goto(INBOX_URL);
    await expect(card(page, UNREVIEWED.id)).toBeVisible();

    await card(page, UNREVIEWED.id).getByRole('button', { name: 'As Asset' }).click();

    const dialog = page.getByTestId('save-as-asset-dialog');
    await expect(dialog).toBeVisible();

    // The suggestion — resolved from `source_asset_id` — is pinned FIRST and
    // labelled. Ordering is the assertion: a suggestion buried in the search
    // results is not a suggestion.
    const candidates = page.getByTestId('sa-candidate');
    await expect(candidates.first()).toBeVisible();
    await expect(candidates.first()).toHaveAttribute('data-asset-id', SUGGESTED_ASSET_ID);
    await expect(candidates.first()).toContainText('suggested');
    await expect(candidates).toHaveCount(ASSET_LIST.length);

    await candidates.first().click();
    const primary = page.getByTestId('sa-primary');
    await expect(primary).toContainText('Sang Yao');
    await primary.click();

    await expect(dialog).toHaveCount(0);
    // The list patched the row in place rather than dropping it — a card
    // vanishing mid-triage reads as a delete.
    await expect(card(page, UNREVIEWED.id)).toHaveAttribute('data-review-state', 'in_assets');

    // …and the server really moved it. Both checks below need an anchor that
    // can ONLY be true of the settled list: a tab click leaves the previous
    // cards on screen until the refetch lands, and renders a spinner (no
    // cards at all) while it is in flight — so a naive "is it here / is it
    // gone" passes against a transient frame either way. Verified: with a
    // stub that does not move the row, this pair fails; the naive pair did not.
    await tab(page, 'In Assets').click();
    // Only ever served by the in_assets tab → its arrival proves the refetch
    // landed and this DOM is that tab's answer.
    await expect(card(page, IN_ASSETS.id)).toBeVisible();
    await expect(card(page, UNREVIEWED.id)).toBeVisible();
    await expect(card(page, UNREVIEWED.id)).toHaveAttribute('data-review-state', 'in_assets');

    await tab(page, 'Unreviewed').click();
    // The empty state renders only when the load has FINISHED and returned
    // nothing — the spinner frame renders neither cards nor this line.
    await expect(page.getByText('Nothing To Review')).toBeVisible();
    await expect(card(page, UNREVIEWED.id)).toHaveCount(0);

    const attachCall = `POST /api/v1/generated/${UNREVIEWED.id}/save-as-asset?scope_id=${SCOPE_ID}`;
    expect(state.requests).toContain(attachCall);
  });

  test('clean-up previews before it deletes, and the preview expires when the window changes', async ({
    page,
  }) => {
    await useEnglish(page);
    await setupStubbedSession(page, { teamId: SCOPE_ID });
    const state = await stubInbox(page);

    await page.goto(INBOX_URL);
    await expect(card(page, UNREVIEWED.id)).toBeVisible();

    await page.getByRole('button', { name: 'Clean Up…' }).click();
    await expect(page.getByRole('heading', { name: 'Clean Up Generated' })).toBeVisible();

    // The confirm is `Delete {{n}}`, and the count is what makes it findable:
    // a bare /^Delete/ would also match the trash button on every card, so a
    // broken gate would look guarded. This locator can only be the dialog's.
    const confirmDelete = page.getByRole('button', { name: /^Delete \d+$/ });

    // No delete affordance before a preview: the whole point of retiring the
    // TTL sweeper was that nothing purges without the user seeing the size.
    await expect(confirmDelete).toHaveCount(0);

    await previewButton(page).click();
    await expect(page.getByTestId('cleanup-sample').first()).toBeVisible();

    await expect(confirmDelete).toBeVisible();
    await expect(confirmDelete).toContainText(String(CLEANUP_DRY.count));

    // Editing the window retracts the quote — a confirm must never outlive
    // the number it was quoting.
    await page.getByLabel('Older Than (Days)').fill('90');
    await expect(confirmDelete).toHaveCount(0);

    await previewButton(page).click();
    await expect(confirmDelete).toBeVisible();
    await confirmDelete.click();

    // The row the live pass removed is gone from the RELOADED list — anchored
    // on the settled empty state, because the reload's spinner frame shows no
    // cards either and would satisfy a bare count on its own.
    await expect(page.getByText('Nothing To Review')).toBeVisible();
    await expect(card(page, UNREVIEWED.id)).toHaveCount(0);
    const live = state.requests.filter((r) => r.includes('/generated/cleanup'));
    expect(live.length).toBe(3); // two previews + one real delete
  });

  test('intermediate canvas inputs are hidden until the Source filter asks for them', async ({
    page,
  }) => {
    await useEnglish(page);
    await setupStubbedSession(page, { teamId: SCOPE_ID });
    const state = await stubInbox(page);

    await page.goto(INBOX_URL);
    await expect(card(page, UNREVIEWED.id)).toBeVisible();
    // Anchored on a card that IS there: "the mask is absent" alone is also
    // true of a page that never loaded.
    await expect(card(page, INTERMEDIATE.id)).toHaveCount(0);
    expect(
      state.requests.some((r) => r.includes('include_intermediate')),
    ).toBe(false);

    await page.getByRole('button', { name: /^Source/ }).click();
    await page.getByRole('menuitemcheckbox', { name: 'Intermediate Inputs' }).click();

    await expect(card(page, INTERMEDIATE.id)).toBeVisible();
    await expect(card(page, UNREVIEWED.id)).toBeVisible();
    expect(
      state.requests.some((r) => r.includes('include_intermediate=true')),
    ).toBe(true);
  });

  test('the thumbnail opens a lightbox with the full file, its metadata and its actions', async ({
    page,
  }) => {
    await useEnglish(page);
    await setupStubbedSession(page, { teamId: SCOPE_ID });
    await stubInbox(page);

    await page.goto(INBOX_URL);
    await expect(card(page, UNREVIEWED.id)).toBeVisible();

    await card(page, UNREVIEWED.id).getByRole('button', { name: /^Preview/ }).click();

    await expect(page.getByTestId('pin-lightbox')).toBeVisible();
    // The FULL file route, not `/cover` — a viewer showing the thumbnail is
    // the failure this replaces, and only the URL can tell them apart.
    await expect(page.getByTestId('pin-lightbox-image')).toHaveAttribute(
      'src',
      new RegExp(`/generated-media/${UNREVIEWED.id}/file$`),
    );

    const meta = page.getByTestId('lightbox-metadata');
    await expect(meta).toBeVisible();
    await expect(meta).toContainText(UNREVIEWED.source.label);
    await expect(meta).toContainText(UNREVIEWED.model);

    const panel = page.getByTestId('pin-lightbox-panel');
    await expect(panel.getByRole('button', { name: /Save To Uploads/ })).toBeVisible();
    await expect(panel.getByRole('button', { name: /As Asset/ })).toBeVisible();

    // Escape still closes — the shared modal's contract survived the
    // extension, which is the whole reason it was extended rather than forked.
    await page.keyboard.press('Escape');
    await expect(page.getByTestId('pin-lightbox')).toHaveCount(0);
  });

  test('the selection control is a circle at the top-left of the thumbnail', async ({
    page,
  }) => {
    await useEnglish(page);
    await setupStubbedSession(page, { teamId: SCOPE_ID });
    await stubInbox(page);

    await page.goto(INBOX_URL);
    const target = card(page, UNREVIEWED.id);
    await expect(target).toBeVisible();

    const check = target.getByRole('checkbox', { name: /^Select/ });
    // Hover first: the control is transparent until then, and a real user
    // cannot click what they cannot see.
    await target.hover();
    await expect(check).toBeVisible();

    const cardBox = await target.boundingBox();
    const checkBox = await check.boundingBox();
    if (!cardBox || !checkBox) throw new Error('no layout box — the card did not render');
    // Left half, top quarter. Geometry rather than class names, because what
    // regressed was where the user's eye goes.
    expect(checkBox.x).toBeLessThan(cardBox.x + cardBox.width / 2);
    expect(checkBox.y).toBeLessThan(cardBox.y + cardBox.height / 4);

    await check.click();
    await expect(page.getByTestId('generated-batch-bar')).toBeVisible();
    // Selecting must not also throw the viewer open over the grid.
    await expect(page.getByTestId('pin-lightbox')).toHaveCount(0);
  });
});
