// e2e/chat-assets.spec.ts
//
// Library ASSETS reaching the chat (P5), through a real browser:
//
//   1. The asset sheet's "Send To Agent" opens the floating chat with the
//      asset staged above the composer, and the turn goes out carrying one
//      `asset_ref` attachment in the exact shape `AttachmentRequest` reads.
//   2. Typing `@` in the composer, switching to the Assets tab and picking a
//      row stages the same thing — and deletes the "@query" the user typed to
//      get there.
//   3. A turn whose attachment the backend could not expand comes back with a
//      typed `attachment_failures` entry, and the banner names the REASON, not
//      just a count.
//
// Why a browser rather than more component tests: every unit suite in this
// feature mocks a module boundary. `AIChatPanel.mentionAssets.test.tsx` mocks
// `assetsService`, so it proves the panel calls the right FUNCTION but never
// that the function builds a request the router answers; the banner's own
// suite is handed a failure array rather than parsing one off a stream. What
// only a browser shows is the two ends meeting: a real `fetch` against
// `/api/v1/assets/search`, a real SSE `done` frame, and a real POST body.
//
// ── Wire shapes ──
// Every fulfilled body is the real one (CLAUDE.md 边界 mock 必须用真实 JSON
// 形状), read off the routers rather than idealized:
//
//   * `/api/v1/assets*` answers the `{success, data}` Envelope and EVERY
//     Snowflake on it is a STRING — `assets_repository._serialize` calls
//     `str()`. `GET /assets/search` answers `Envelope[List[AssetResponse]]`,
//     i.e. the same row the shelf list emits, so the fixtures are shared with
//     `assets-codex.spec.ts`. `in_library` is spread on here because the
//     captured bodies predate mig 449 and `AssetResponse` now REQUIRES it.
//   * `/api/v1/ai-library/*` list endpoints answer BARE bodies, no envelope.
//   * `/sessions/{id}/chat-stream` is SSE: `event: NAME\ndata: JSON\n\n`,
//     terminated by a `done` frame — `aiLibraryService.streamChatMessage`
//     stops reading at `done` or `error` and at nothing else.
//   * `attachment_failures` rides on the `done` frame's data, as
//     `{index, kind, reason}` (ruling C's closed vocabulary on the asset path).

import { expect, test, type Page, type Route } from '@playwright/test';

import { setupStubbedSession } from './helpers/stubs';
import {
  ASSETS,
  LOCATION,
  PNG_1X1,
  READY,
  READY_DETAIL,
  SCOPE_ID,
  SEARCH_RESULTS,
  type WireAsset,
} from './fixtures/assets';

const SHEET_URL = `/team/${SCOPE_ID}/resources/assets/item/${READY.id}`;
const AGENT_SLUG = 'analyze';
const SESSION_ID = 'sess-e2e-assets';

/**
 * What the panel reloads after a turn — BOTH rows, exactly as the backend
 * persists them.
 *
 * The assistant row is not decoration. `handleSend` refetches the whole
 * history when the stream ends and REPLACES the streamed bubble with what the
 * server says; a history holding only the user turn therefore erases the reply
 * the user just watched arrive. That made the "no banner" case fail whenever
 * the reload beat the assertion, and it was failing on the half of the test
 * that only sets the scene — so the negative control was proving nothing.
 *
 * Shapes are `MessageOut` (`schemas/ai_library_chat.py:78-101`): ids are
 * STRINGS, and `attachments` is None for assistant rows — the schema comment
 * says so in as many words, so a fixture that gave the assistant an empty array
 * would be a shape the backend cannot produce.
 */
function persistedTurns(text: string, reply: string) {
  return [
    {
      id: '727145299382500001',
      session_id: SESSION_ID,
      role: 'user',
      content: text,
      // The persisted attachment shape, already narrowed by the store's
      // `_DISPLAY_ATTACHMENT_KEYS` whitelist — a bubble built from the
      // OUTGOING payload would pass here and render blank in production.
      //
      // FOUR keys, not five: `loadout_id` is whitelisted server-side but the
      // reducer drops nulls (`if a.get(k) is not None`) and v1 always sends
      // null, so no persisted row can carry it. `url` is absent for the same
      // reason it is absent from the whitelist. Verified against the real
      // reducer, not inferred (final review M2).
      attachments: [
        {
          kind: 'asset_ref',
          asset_id: READY.id,
          name: READY.name,
          mime: '',
        },
      ],
      prompt_tokens: null,
      completion_tokens: null,
      created_at: '2026-09-03T00:00:00+00:00',
    },
    {
      id: '727145299382500002',
      session_id: SESSION_ID,
      role: 'assistant',
      content: reply,
      attachments: null,
      prompt_tokens: 120,
      completion_tokens: 3,
      created_at: '2026-09-03T00:00:01+00:00',
    },
  ];
}

/** The one line the stubbed model "says". Asserted after the history reload,
 *  so it has to survive that reload. */
const REPLY = 'Noted.';

interface ChatState {
  /** Every `/chat-stream` POST body, in order. */
  sent: Array<Record<string, unknown>>;
  /** Every `/assets/search` request line, so a test can assert what was ASKED. */
  searches: string[];
  /** What the next `done` frame carries. */
  failures: Array<{ index: number; kind: string; reason: string }>;
  /** Server-side history, grown by a send. */
  messages: unknown[];
}

function envelope(route: Route, data: unknown): Promise<void> {
  return route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ success: true, data }),
  });
}

function bare(route: Route, body: unknown): Promise<void> {
  return route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
}

/** `AssetResponse` as the router emits it today. */
function searchRow(row: WireAsset) {
  return { ...row, in_library: true };
}

/**
 * i18n defaults to 'zh' when no `language` key is stored (see `i18n.ts`), so
 * the English labels this spec asserts on only exist once it is pinned. Same
 * seeding the codex and canvas suites do.
 */
async function pinEnglishLocale(page: Page): Promise<void> {
  await page.addInitScript(() => {
    try {
      localStorage.setItem('language', 'en');
    } catch {
      /* localStorage unavailable — nothing we can do */
    }
  });
}

async function stubAll(page: Page): Promise<ChatState> {
  const state: ChatState = { sent: [], searches: [], failures: [], messages: [] };

  // The sheet's generation-history panel. Explicit rather than left to the
  // session harness's catch-all, which answers `data: []` — the panel reads
  // `items` off it, and an "unavailable" panel from a fixture gap would be
  // indistinguishable from the real failure state.
  await page.route('**/api/v1/generated**', (route) =>
    envelope(route, { items: [], next_cursor: null }),
  );

  // BARE body, no envelope — `resources_search_router` does not use one.
  await page.route('**/api/v1/resources/search*', (route) => bare(route, SEARCH_RESULTS));

  await page.route('**/api/v1/resources/*/cover*', (route) =>
    route.fulfill({ status: 200, contentType: 'image/png', body: PNG_1X1 }),
  );

  // One handler for the whole asset router: ordering between `page.route`
  // registrations is last-wins, and a `/assets/search` route that quietly
  // stopped winning would answer the list body instead — which is a valid
  // envelope, so nothing would fail loudly.
  await page.route('**/api/v1/assets**', (route) => {
    const url = new URL(route.request().url());
    const p = url.pathname;

    if (p.endsWith('/assets/search')) {
      state.searches.push(`${p}${url.search}`);
      const q = (url.searchParams.get('q') ?? '').toLowerCase();
      const type = url.searchParams.get('type');
      const rows = ASSETS.filter(
        (row) =>
          (!q || row.name.toLowerCase().includes(q)) &&
          (type === null || row.asset_type === type),
      ).map(searchRow);
      return envelope(route, rows);
    }

    if (p.endsWith('/assets/counts')) {
      const counts: Record<string, number> = {
        character: 0, location: 0, prop: 0, costume: 0, prompt: 0, audio: 0,
      };
      for (const row of ASSETS) {
        if (!row.is_system_preset) counts[row.asset_type] += 1;
      }
      return envelope(route, counts);
    }

    if (p.endsWith(`/assets/${READY.id}`)) return envelope(route, READY_DETAIL);
    if (p.endsWith('/assets')) return envelope(route, ASSETS.map(searchRow));
    return envelope(route, []);
  });

  // ── AI library. List endpoints answer BARE bodies. ────────────────────────
  await page.route('**/api/v1/ai-library/agents', (route) =>
    bare(route, [
      { id: '1', slug: AGENT_SLUG, name: 'Analyze', enabled: true, is_system_preset: true },
    ]),
  );
  await page.route('**/api/v1/ai-library/agents/*/sessions*', (route) => {
    if (route.request().method() === 'POST') {
      return bare(route, { id: SESSION_ID, title: 'New conversation', agent_slug: AGENT_SLUG });
    }
    return bare(route, [{ id: SESSION_ID, title: 'New conversation', agent_slug: AGENT_SLUG }]);
  });
  await page.route('**/api/v1/ai-library/sessions?*', (route) => bare(route, []));
  await page.route(`**/api/v1/ai-library/sessions/${SESSION_ID}`, (route) =>
    bare(route, {
      id: SESSION_ID,
      title: 'New conversation',
      agent_slug: AGENT_SLUG,
      messages: state.messages,
    }),
  );

  await page.route(`**/api/v1/ai-library/sessions/${SESSION_ID}/chat-stream`, (route) => {
    const body = JSON.parse(route.request().postData() ?? '{}') as Record<string, unknown>;
    state.sent.push(body);
    state.messages = persistedTurns(String(body.content ?? ''), REPLY);
    const done = JSON.stringify(
      state.failures.length > 0 ? { attachment_failures: state.failures } : {},
    );
    return route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body:
        `event: delta\ndata: ${JSON.stringify({ text: REPLY })}\n\n` +
        `event: done\ndata: ${done}\n\n`,
    });
  });

  return state;
}

async function openSheet(page: Page): Promise<ChatState> {
  await pinEnglishLocale(page);
  await setupStubbedSession(page, { teamId: SCOPE_ID });
  const state = await stubAll(page);
  await page.goto(SHEET_URL);
  await expect(page.getByTestId('sheet-sidebar')).toBeVisible({ timeout: 20_000 });
  return state;
}

/**
 * Type "@" and then the query, in two steps.
 *
 * `ChatInput` announces the bare "@" from a `setTimeout(…, 0)` so the
 * character lands first, and announces each subsequent character from tiptap's
 * synchronous update handler. Typed in one uninterrupted burst — which is what
 * `keyboard.type` with no delay is — every keystroke's update runs BEFORE that
 * macrotask, so the "@" announcement arrives last and resets the query to
 * empty. A person cannot type inside one macrotask; a paste can, and that gap
 * is noted in the PR's known items rather than papered over here. Splitting
 * the input is what a human keyboard actually looks like.
 */
async function typeMention(page: Page, prefix: string, query: string): Promise<void> {
  await page.keyboard.type(prefix);
  await expect(page.getByTestId('resource-picker')).toBeVisible();
  if (query) await page.keyboard.type(query, { delay: 20 });
}

/** The floating chat, opened by "Send To Agent" and ready to type into. */
async function chatPanel(page: Page) {
  const panel = page.getByTestId('sb-panel-chat');
  await expect(panel).toBeVisible();
  await expect(panel.locator('[contenteditable="true"]')).toBeVisible();
  return panel;
}

// ── 1. Send To Agent ─────────────────────────────────────────────────────────

test('the sheet\'s Send To Agent stages the asset above the composer', async ({ page }) => {
  await openSheet(page);

  await page.getByTestId('send-to-agent').click();

  const panel = await chatPanel(page);
  const chip = panel.getByTestId('staged-asset-chip');
  await expect(chip).toBeVisible();
  await expect(chip).toContainText(READY.name);
});

test('the staged asset goes out as one asset_ref with exactly six keys', async ({ page }) => {
  const state = await openSheet(page);
  await page.getByTestId('send-to-agent').click();
  const panel = await chatPanel(page);
  await expect(panel.getByTestId('staged-asset-chip')).toBeVisible();

  await panel.locator('[contenteditable="true"]').fill('keep her consistent');
  await panel.getByTitle('Send message').click();

  await expect.poll(() => state.sent.length).toBe(1);
  const attachments = state.sent[0].attachments as Record<string, unknown>[];
  expect(attachments).toHaveLength(1);
  // The three composer-side snapshots (`asset_type`, `cover_file_id`,
  // `scope_id`) must NOT be on the wire: the server re-resolves the asset by
  // id and re-checks access by team membership, so sending them would state as
  // fact something the receiver ignores.
  expect(Object.keys(attachments[0]).sort()).toEqual([
    'asset_id', 'kind', 'loadout_id', 'mime', 'name', 'url',
  ]);
  expect(attachments[0]).toEqual({
    kind: 'asset_ref',
    asset_id: READY.id,
    // v1 has no loadout picker at either entry point; null is the backend's
    // "use the default loadout", not a missing value.
    loadout_id: null,
    name: READY.name,
    mime: '',
    url: '',
  });
});

// ── 2. The @ picker's Assets tab ─────────────────────────────────────────────

test('@ → Assets tab lists what the membership-wide search returned', async ({ page }) => {
  const state = await openSheet(page);
  await page.getByTestId('send-to-agent').click();
  const panel = await chatPanel(page);
  // Start from a clean composer: this test is about the picker, and the chip
  // Send To Agent left behind would make the staging assertion ambiguous.
  await panel.getByTestId('staged-asset-chip').getByLabel('remove').click();

  await panel.locator('[contenteditable="true"]').click();
  await typeMention(page, 'look at @', 'sang');

  await page.getByTestId('resource-picker-tab-assets').click();
  const options = page.getByTestId('mention-asset-option');
  await expect(options.first()).toBeVisible();
  await expect(options.first()).toContainText(READY.name);

  // Poll for the request carrying the SETTLED query rather than reading
  // whichever one is last at this instant: the box debounces, so "the most
  // recent request" and "the request for what the user typed" are not the same
  // thing until it has caught up.
  await expect
    .poll(() => state.searches.find((line) => line.includes('q=sang')) ?? null)
    .not.toBeNull();
  const search = state.searches.find((line) => line.includes('q=sang')) as string;
  // `in`, not `all`: the shelf answers "my library", and a library member is
  // one somebody ADDED (mig 449). The grid's own pill is what widens it back
  // over script imports and migrated legacy cards.
  expect(search).toContain('library=in');
  // No `scope_id` on ANY of them: a chat window outlives any one workspace
  // route, so the server authorizes by membership instead (ruling B/G). A
  // picker that sent one would be answering a question it does not own.
  for (const line of state.searches) expect(line).not.toContain('scope_id');
});

test('picking from the Assets tab stages the asset and deletes the @query', async ({ page }) => {
  await openSheet(page);
  await page.getByTestId('send-to-agent').click();
  const panel = await chatPanel(page);
  await panel.getByTestId('staged-asset-chip').getByLabel('remove').click();

  const editor = panel.locator('[contenteditable="true"]');
  await editor.click();
  await typeMention(page, 'look at @', 'sang');
  await page.getByTestId('resource-picker-tab-assets').click();
  await expect(page.getByTestId('mention-asset-option').first()).toBeVisible();

  await page.getByTestId('mention-asset-option').first().click();

  await expect(panel.getByTestId('staged-asset-chip')).toContainText(READY.name);
  // Left behind, "@sang" would be sent as message body — the asset is staged
  // above the composer now, so the text stands for nothing.
  await expect(editor).toHaveText('look at ');
  await expect(page.getByTestId('resource-picker')).toHaveCount(0);
});

test('the type chips narrow the Assets tab to one kind', async ({ page }) => {
  const state = await openSheet(page);
  await page.getByTestId('send-to-agent').click();
  const panel = await chatPanel(page);

  await panel.locator('[contenteditable="true"]').click();
  await typeMention(page, '@', '');
  await page.getByTestId('resource-picker-tab-assets').click();
  await expect(page.getByTestId('mention-asset-option').first()).toBeVisible();

  await page.locator('[data-testid="mention-type-chip"][data-type="location"]').click();

  await expect
    .poll(() => state.searches.some((line) => line.includes('type=location')))
    .toBe(true);
  await expect(page.getByTestId('mention-asset-option')).toHaveCount(1);
  await expect(page.getByTestId('mention-asset-option').first()).toContainText(LOCATION.name);
});

// ── 3. Typed failure回显 ─────────────────────────────────────────────────────

test('a typed attachment failure names the reason, not just a count', async ({ page }) => {
  const state = await openSheet(page);
  // Ruling C's vocabulary. The generic counted line is what a code with no
  // copy falls back to; a reason with copy must never land there.
  state.failures = [{ index: 0, kind: 'asset_ref', reason: 'asset_no_primary_image' }];

  await page.getByTestId('send-to-agent').click();
  const panel = await chatPanel(page);
  await expect(panel.getByTestId('staged-asset-chip')).toBeVisible();
  await panel.locator('[contenteditable="true"]').fill('keep her consistent');
  await panel.getByTitle('Send message').click();

  await expect(panel.getByText('123 tokens')).toBeVisible();
  const reason = panel.getByTestId('attachment-failure-reason');
  await expect(reason).toBeVisible();
  await expect(reason).toHaveAttribute('data-reason', 'asset_no_primary_image');
  await expect(reason).toContainText(/image/i);
  // An untranslated identifier in a user-facing banner is the failure mode the
  // banner exists to prevent, not a lesser version of it.
  await expect(reason).not.toContainText('asset_no_primary_image');
});

test('a clean turn shows no failure banner at all', async ({ page }) => {
  // The negative control. Without it, a banner that rendered unconditionally
  // would pass the case above and cry wolf on every turn.
  const state = await openSheet(page);
  state.failures = [];

  await page.getByTestId('send-to-agent').click();
  const panel = await chatPanel(page);
  await expect(panel.getByTestId('staged-asset-chip')).toBeVisible();
  await panel.locator('[contenteditable="true"]').fill('keep her consistent');
  await panel.getByTitle('Send message').click();

  // Wait for the HISTORY RELOAD, not just for the streamed delta: `handleSend`
  // refetches when the stream ends and replaces the streamed bubble with the
  // server's rows, and only the persisted assistant row carries a token count.
  // Asserting before that lands would let a reload that erased the reply pass.
  await expect(panel.getByText('123 tokens')).toBeVisible();
  await expect(panel.getByText(REPLY)).toBeVisible();
  await expect(panel.getByTestId('attachment-failure-reason')).toHaveCount(0);
});
