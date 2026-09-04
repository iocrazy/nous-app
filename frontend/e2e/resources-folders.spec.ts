// frontend/e2e/resources-folders.spec.ts
//
// The My Uploads root folders grid, against real wire bodies.
//
// P6 retired a name filter that dropped one folder from this grid
// (`filteredFolders.filter((f) => f.name !== 'temp')` in ResourceGrid). The
// chat-attachment folder now has an identity instead of a machine name —
// migration `..._chat_uploads_system_folder.sql` adopts it as
// `system_key='chat_uploads'`, `is_system=true`, `name='Chat Uploads'` — so
// it belongs in the grid like any other system folder, with `is_system`
// carrying the protection the hidden-ness used to imply.
//
// Why an e2e on top of the component tests: the component tests hand
// ResourceGrid an already-normalized `Folder[]` prop. Nothing in them proves
// the row survives the trip from the wire, and the context menu is assembled
// three components away from the grid. This spec drives the real
// ResourcesView with real response bodies and right-clicks the real card.
//
// WIRE SHAPE (CLAUDE.md 开发规范 "边界 mock 必须用真实 JSON 形状"): folders
// reach this view through PostgREST, not the FastAPI router —
// `resourceService.fetchFolders` runs `supabase.from('folders').select('*')`.
// PostgREST serialises a BIGINT column as a JSON **number**, so `id`,
// `scope_id`, `parent_id` and `library_id` are numbers in the body below, not
// strings. (The FastAPI side agrees: `/api/v1/resources/folders/list` returns
// `_folder_row_to_dict` → `_rest_parity`, which coerces uuid and datetime but
// leaves ints native.) The strings the app works with are produced *after*
// the response, by `bigIntSafeFetch` in supabaseClient.ts, which quotes any
// bare 16-or-more-digit integer before parse — a fixture that pre-stringified
// these ids would skip the very conversion this view depends on.

import { test, expect, type Page } from '@playwright/test';

import { setupStubbedSession } from './helpers/stubs';
import { SCOPE_ID } from './fixtures/generated';

const RESOURCES_URL = `/team/${SCOPE_ID}/resources`;

const USER_ID = '00000000-0000-4000-8000-000000000001';

// A snowflake does not survive a round trip through a JS `number` — 2^53 is
// two digits short — so bigint columns are carried as `BIGINT:<digits>`
// markers and unquoted by `wireBody` below. Serialising them as numbers here
// would silently round the id the fixture claims to be sending.
const bigint = (digits: string) => `BIGINT:${digits}`;

/** Every column of `public.folders`, in PostgREST's `select('*')` shape. */
function folderRow(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: bigint('742318905233408001'),
    name: 'Folder',
    scope_id: bigint(SCOPE_ID),
    created_by: USER_ID,
    sort_order: 0,
    is_system: false,
    system_key: null,
    visibility: 'inherited',
    is_trashed: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    icon: null,
    color: null,
    trashed_at: null,
    parent_id: null,
    library_id: null,
    is_smart: false,
    smart_rules: null,
    ...over,
  };
}

/** JSON text with the `BIGINT:` markers turned back into bare numbers. */
function wireBody(rows: Record<string, unknown>[]): string {
  return JSON.stringify(rows).replace(/"BIGINT:(\d+)"/g, '$1');
}

/** The adopted chat-attachment folder — a system folder after the migration. */
const CHAT_UPLOADS = folderRow({
  id: bigint('742318905233408002'),
  name: 'Chat Uploads',
  is_system: true,
  system_key: 'chat_uploads',
  sort_order: 1,
});

/** An ordinary user folder — the control the system-folder assertions need. */
const PLAIN = folderRow({ id: bigint('742318905233408004'), name: 'Reference Boards' });

/**
 * A folder still literally named `temp`. The migration adopts at most ONE per
 * scope, and only one whose `system_key` is still NULL, so a second one — or
 * a scope the migration has not reached — keeps a plain user folder by that
 * name. It is here so a regression that reinstated the name filter turns this
 * spec red: the adopted folder is called "Chat Uploads" now and would sail
 * straight through such a filter.
 */
const LEGACY_TEMP = folderRow({ id: bigint('742318905233408003'), name: 'temp', sort_order: 2 });

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

async function stubFolders(page: Page): Promise<void> {
  // Registered AFTER setupStubbedSession so it beats that harness's
  // `**/rest/v1/**` → [] catch-all (Playwright checks handlers in reverse
  // registration order).
  await page.route('**/rest/v1/folders*', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: wireBody([PLAIN, CHAT_UPLOADS, LEGACY_TEMP]),
    }),
  );
}

/** The grid card for a folder, by its visible name. */
function folderCard(page: Page, name: string) {
  return page.locator('[data-context-item]').filter({ hasText: name }).first();
}

/** One context-menu entry, matched on its whole label. */
function menuItem(page: Page, label: string) {
  return page.getByRole('button', { name: label, exact: true });
}

test.describe('My Uploads root — folders grid', () => {
  test.beforeEach(async ({ page }) => {
    await useEnglish(page);
    await setupStubbedSession(page, { teamId: SCOPE_ID });
    await stubFolders(page);
  });

  test('the Chat Uploads system folder is in the root grid', async ({ page }) => {
    await page.goto(RESOURCES_URL);

    await expect(folderCard(page, 'Reference Boards')).toBeVisible();
    await expect(folderCard(page, 'Chat Uploads')).toBeVisible();
    await expect(folderCard(page, 'temp')).toBeVisible();
  });

  test('its context menu offers no Rename or Move, and says why', async ({ page }) => {
    await page.goto(RESOURCES_URL);

    await folderCard(page, 'Chat Uploads').click({ button: 'right' });

    // The menu is open — assert that before asserting what is missing, so an
    // unopened menu can never read as "the entries are correctly absent".
    await expect(menuItem(page, 'Get Info')).toBeVisible();

    await expect(menuItem(page, 'Rename')).toHaveCount(0);
    await expect(menuItem(page, 'Move To')).toHaveCount(0);
    await expect(menuItem(page, 'Move to Trash')).toHaveCount(0);

    await expect(
      menuItem(page, 'System folder — cannot be renamed, moved or trashed'),
    ).toBeVisible();
    // Reading the folder is never blocked — only the four mutations are.
    await expect(menuItem(page, 'Copy To')).toBeVisible();
  });

  test('a plain folder still offers Rename and Move', async ({ page }) => {
    await page.goto(RESOURCES_URL);

    await folderCard(page, 'Reference Boards').click({ button: 'right' });

    await expect(menuItem(page, 'Rename')).toBeVisible();
    await expect(menuItem(page, 'Move To')).toBeVisible();
    await expect(menuItem(page, 'Move to Trash')).toBeVisible();
    await expect(
      menuItem(page, 'System folder — cannot be renamed, moved or trashed'),
    ).toHaveCount(0);
  });
});
