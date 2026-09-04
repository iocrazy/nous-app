// frontend/e2e/resources-as-asset.spec.ts
//
// "As Asset" from the My Uploads context menu (P6 ruling E), against real wire
// bodies and the real components.
//
// The unit suites each prove one link: the menu offers the entry for the right
// kinds, the host opens the dialog, the dialog submits to the right endpoint.
// None of them proves the chain survives the trip from the wire — the resource
// row reaches the card through PostgREST, the card reaches the menu through
// three components, and the id the dialog sends has been through
// `bigIntSafeFetch` on the way. This spec drives that whole path and asserts on
// the REQUEST the browser actually made.
//
// WIRE SHAPES (CLAUDE.md 开发规范 "边界 mock 必须用真实 JSON 形状"), and they
// differ per hop on purpose:
//
//   * `resource_items` + the embedded `resources` row come from PostgREST
//     (`resourceService.fetchResourcesPaginated` →
//     `supabase.from('resource_items').select('*, resource:resources!inner(...)')`).
//     PostgREST serialises BIGINT as a JSON **number**, so `id`, `scope_id`,
//     `resource_id` and `folder_id` are numbers below — carried as `BIGINT:`
//     markers because a snowflake does not survive a JS `number` literal, and
//     unquoted by `wireBody`. The strings the app uses are produced AFTER the
//     response by `bigIntSafeFetch`; a pre-stringified fixture would skip the
//     very conversion this path depends on.
//   * `/api/v1/assets` and `/api/v1/resources/{id}/save-as-asset` come from
//     FastAPI, whose `AssetResponse` / the save-as-asset envelope declare every
//     id as `str`. Those are **strings**.
//
// Same class of id, two shapes, both real. That is exactly the trap the
// storyboard-canvas P0 fell into.

import { test, expect, type Page, type Request } from '@playwright/test';

import { setupStubbedSession } from './helpers/stubs';
import { SCOPE_ID } from './fixtures/generated';

const RESOURCES_URL = `/team/${SCOPE_ID}/resources`;
const USER_ID = '00000000-0000-4000-8000-000000000001';

const IMAGE_RESOURCE_ID = '742318905233409001';
const VIDEO_RESOURCE_ID = '742318905233409002';
const ASSET_ID = '742318905233410001';

const bigint = (digits: string) => `BIGINT:${digits}`;

/** JSON text with the `BIGINT:` markers turned back into bare numbers. */
function wireBody(value: unknown): string {
  return JSON.stringify(value).replace(/"BIGINT:(\d+)"/g, '$1');
}

/** A `resources` row as PostgREST embeds it under `resource:`. */
function resourceRow(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: bigint(IMAGE_RESOURCE_ID),
    creator_id: USER_ID,
    source_type: 'upload',
    media_id: null,
    filename: 'sang-yao-turnaround.png',
    file_type: 'image',
    mime_type: 'image/png',
    file_path: 'uploads/sang-yao-turnaround.png',
    file_size_bytes: 204800,
    duration_seconds: null,
    resolution: '1024x1024',
    thumbnail_path: null,
    cover_image_path: null,
    current_version: 1,
    notes: null,
    gen_prompt: null,
    gen_params: null,
    url: null,
    rating: 0,
    transcript_status: 'none',
    summary_status: 'none',
    visual_analysis_status: 'none',
    is_trashed: false,
    trashed_at: null,
    created_at: '2026-09-01T04:15:00Z',
    updated_at: '2026-09-01T04:15:00Z',
    media: null,
    ...over,
  };
}

/** A `resource_items` row, PostgREST `select('*, resource:resources!inner(...)')`. */
function itemRow(itemId: string, resource: Record<string, unknown>): Record<string, unknown> {
  return {
    id: bigint(itemId),
    resource_id: resource.id,
    scope_id: bigint(SCOPE_ID),
    folder_id: null,
    library_id: null,
    added_by: USER_ID,
    created_at: '2026-09-01T04:15:00Z',
    resource,
  };
}

const IMAGE_ITEM = itemRow('742318905233500001', resourceRow());
const VIDEO_ITEM = itemRow(
  '742318905233500002',
  resourceRow({
    id: bigint(VIDEO_RESOURCE_ID),
    filename: 'clip.mp4',
    file_type: 'video',
    mime_type: 'video/mp4',
    duration_seconds: 12,
  }),
);

/** `AssetResponse` (app/schemas/assets.py) — every id a STRING. */
const YI_HENG = {
  id: ASSET_ID,
  scope_id: SCOPE_ID,
  asset_type: 'character',
  subtype: null,
  name: 'Yi Heng',
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
  in_library: true,
  tags: {},
  sort_order: 0,
  created_by: USER_ID,
  created_at: '2026-08-30T00:00:00Z',
  updated_at: '2026-08-30T00:00:00Z',
  readiness: { state: 'draft', missing: ['sheet'] },
  file_counts_by_slot: {},
  project_ids: [],
  loadout_count: 0,
};

/** `AssetDetailResponse` — what the loadout fetch reads. No loadouts here, so
 *  the request carries no `loadout_id` and the assertion below can be exact. */
const YI_HENG_DETAIL = {
  ...YI_HENG,
  files: [],
  links: [],
  linked_by: [],
  loadouts: [],
  used_in: null,
};

/** The route's own 201 envelope: the generation shape plus `generated_id`. */
const SAVE_AS_ASSET_201 = {
  success: true,
  data: {
    generation: {
      id: '742318905233411001',
      scope_id: SCOPE_ID,
      media_kind: 'image',
      mime: 'image/png',
      prompt: null,
      model: null,
      provider: null,
      origin_kind: 'library_upload',
      canvas_id: null,
      node_id: null,
      created_at: '2026-09-04T09:00:00Z',
      promoted_resource_id: IMAGE_RESOURCE_ID,
      review_state: 'in_assets',
      source_asset_id: null,
      source: {
        kind: 'library_upload',
        label: 'Library upload',
        canvas_id: null,
        node_id: null,
        shot_id: null,
        conversation_id: null,
        deep_link: null,
      },
      title: 'sang-yao-turnaround.png',
    },
    asset_id: ASSET_ID,
    resource_id: IMAGE_RESOURCE_ID,
    generated_id: '742318905233411001',
  },
};

/** A typed 422 from the same route, `_err` shape. */
const KIND_UNSUPPORTED_422 = {
  success: false,
  error: {
    code: 'resource_kind_unsupported',
    detail:
      "Only image and audio resources can be saved as an asset; this one is 'video/mp4'",
  },
};

/**
 * i18n defaults to 'zh' when no `language` key is stored (see `i18n.ts`), so
 * the English labels this spec asserts on only exist once it is pinned.
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

/**
 * Registered AFTER `setupStubbedSession` so they beat its `**\/rest\/v1\/**`
 * and `**\/api\/v1\/**` catch-alls (Playwright checks handlers in reverse
 * registration order).
 */
async function stubLibrary(
  page: Page,
  opts: { saveAsAsset?: { status: number; body: unknown } } = {},
): Promise<Request[]> {
  const saveRequests: Request[] = [];

  await page.route('**/rest/v1/resource_items*', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: wireBody([IMAGE_ITEM, VIDEO_ITEM]),
    }),
  );

  // The picker's search. Registered before the by-id route below so the more
  // specific one wins.
  await page.route('**/api/v1/assets?**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: [YI_HENG] }),
    }),
  );
  await page.route(`**/api/v1/assets/${ASSET_ID}?**`, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: YI_HENG_DETAIL }),
    }),
  );

  const answer = opts.saveAsAsset ?? { status: 201, body: SAVE_AS_ASSET_201 };
  await page.route('**/api/v1/resources/*/save-as-asset*', (route) => {
    saveRequests.push(route.request());
    return route.fulfill({
      status: answer.status,
      contentType: 'application/json',
      body: JSON.stringify(answer.body),
    });
  });

  return saveRequests;
}

/** The grid card for a file, by its visible filename. */
function fileCard(page: Page, filename: string) {
  return page.locator('[data-context-item]').filter({ hasText: filename }).first();
}

/** One context-menu entry, matched on its whole label. */
function menuItem(page: Page, label: string) {
  return page.getByRole('button', { name: label, exact: true });
}

test.describe('My Uploads — As Asset', () => {
  test.beforeEach(async ({ page }) => {
    await useEnglish(page);
    await setupStubbedSession(page, { teamId: SCOPE_ID });
  });

  test('attaches an image to an asset and reports it', async ({ page }) => {
    const saveRequests = await stubLibrary(page);
    await page.goto(RESOURCES_URL);

    await fileCard(page, 'sang-yao-turnaround.png').click({ button: 'right' });
    // Assert the menu is open before clicking into it, so a miss reads as a
    // missing entry rather than a timeout on an unopened menu.
    await expect(menuItem(page, 'Send to Agent')).toBeVisible();
    await menuItem(page, 'As Asset').click();

    const dialog = page.getByTestId('save-as-asset-dialog');
    await expect(dialog).toBeVisible();
    // Prefilled from the resource, not from a generation.
    await expect(dialog.getByText('sang-yao-turnaround.png')).toBeVisible();
    await expect(dialog.getByText('My Uploads')).toBeVisible();

    await dialog.getByText('Yi Heng').click();
    await page.getByTestId('sa-primary').click();

    await expect.poll(() => saveRequests.length).toBe(1);
    const request = saveRequests[0];
    const url = new URL(request.url());
    // The RESOURCE route, carrying the scope the asset lives in.
    expect(url.pathname).toBe(`/api/v1/resources/${IMAGE_RESOURCE_ID}/save-as-asset`);
    expect(url.searchParams.get('scope_id')).toBe(SCOPE_ID);
    expect(request.method()).toBe('POST');
    expect(request.postDataJSON()).toEqual({ asset_id: ASSET_ID, slot: 'unsorted' });

    await expect(page.getByText('Attached to Yi Heng · Unsorted')).toBeVisible();
    await expect(dialog).toHaveCount(0);
  });

  test('a refused kind says WHY, and leaves the dialog open', async ({ page }) => {
    const saveRequests = await stubLibrary(page, {
      saveAsAsset: { status: 422, body: KIND_UNSUPPORTED_422 },
    });
    await page.goto(RESOURCES_URL);

    await fileCard(page, 'sang-yao-turnaround.png').click({ button: 'right' });
    await menuItem(page, 'As Asset').click();

    const dialog = page.getByTestId('save-as-asset-dialog');
    await expect(dialog).toBeVisible();
    await dialog.getByText('Yi Heng').click();
    await page.getByTestId('sa-primary').click();

    await expect.poll(() => saveRequests.length).toBe(1);
    // The typed copy for THIS code — not the generic "something went wrong",
    // which would leave the user with nothing to act on.
    await expect(
      page.getByText('Only images and audio can be attached to an asset slot.'),
    ).toBeVisible();
    // A refusal is not a completion: the dialog stays on the answers already
    // given so a fixable reason can be fixed.
    await expect(dialog).toBeVisible();
  });

  test('a video row is not offered the entry at all', async ({ page }) => {
    await stubLibrary(page);
    await page.goto(RESOURCES_URL);

    await fileCard(page, 'clip.mp4').click({ button: 'right' });

    // The menu is open — assert that before asserting what is missing.
    await expect(menuItem(page, 'Send to Agent')).toBeVisible();
    await expect(menuItem(page, 'As Asset')).toHaveCount(0);
  });

  test('cancelling sends nothing — no inbox row is minted for it', async ({ page }) => {
    const saveRequests = await stubLibrary(page);
    await page.goto(RESOURCES_URL);

    await fileCard(page, 'sang-yao-turnaround.png').click({ button: 'right' });
    await menuItem(page, 'As Asset').click();

    const dialog = page.getByTestId('save-as-asset-dialog');
    await expect(dialog).toBeVisible();
    await dialog.getByText('Yi Heng').click();
    await page.getByRole('button', { name: 'Cancel', exact: true }).click();

    await expect(dialog).toHaveCount(0);
    expect(saveRequests).toHaveLength(0);
  });
});
