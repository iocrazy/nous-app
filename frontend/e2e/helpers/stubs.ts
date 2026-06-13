import { type Page, type Route } from '@playwright/test';

/**
 * Full-stub harness for the Storyboard editor E2E suite.
 *
 * The storyboard editor persists through `/api/v1/storyboard/*` (FastAPI) and
 * resolves teams through Supabase REST (`/rest/v1/*`). MediaHub has no runnable
 * dev backend (FastAPI is prod-only), so these tests intercept the entire
 * backend at the network layer with `page.route` and seed an authenticated
 * Supabase session directly into `localStorage`. The result is deterministic,
 * sub-second, zero-cost, and CI-ready — no real backend, no test account.
 *
 * Storage key contract: the app builds the Supabase auth storage key as
 * `sb-${hostname.split('.')[0]}-auth-token` from `VITE_SUPABASE_URL`. The
 * playwright webServer pins `VITE_SUPABASE_URL=https://e2e.supabase.co` at
 * build time, so the key is deterministically `sb-e2e-auth-token`.
 */

export const TEAM_ID = 'team-e2e';
export const PARENT_PROJECT_ID = 'proj-e2e';
export const STORYBOARD_ID = 'sb-e2e';
export const USER_ID = '00000000-0000-4000-8000-000000000001';
export const STORAGE_KEY = 'sb-e2e-auth-token';

/** Direct editor URL — bypasses the multi-level create UI (data is stubbed). */
export const EDITOR_URL = `/team/${TEAM_ID}/projects/${PARENT_PROJECT_ID}/storyboard/${STORYBOARD_ID}`;

/**
 * 2×2 red PNG (same bytes as e2e/fixtures/frame.png) inlined as a data URL, so
 * stubbed `image_url` responses render in <img> without an extra round-trip.
 */
export const FIXTURE_IMAGE_URL =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEUlEQVR4nGP8z8Dwn4EIwAhUBADtgwXqWkAdRgAAAABJRU5ErkJggg==';

const ONE_YEAR_SECONDS = 365 * 24 * 60 * 60;

const TEAM = {
  id: TEAM_ID,
  name: 'E2E Personal Team',
  kind: 'personal',
  enabled_modules: null,
  owner_id: USER_ID,
  created_at: '2020-01-01T00:00:00Z',
  updated_at: '2020-01-01T00:00:00Z',
};

function base64url(value: object): string {
  return Buffer.from(JSON.stringify(value))
    .toString('base64')
    .replace(/=/g, '')
    .replace(/\+/g, '-')
    .replace(/\//g, '_');
}

/** A structurally-valid (unsigned) JWT. Stubbed endpoints never verify it. */
function fakeJwt(expSeconds: number): string {
  const header = base64url({ alg: 'HS256', typ: 'JWT' });
  const payload = base64url({
    sub: USER_ID,
    aud: 'authenticated',
    role: 'authenticated',
    email: 'e2e@example.com',
    exp: expSeconds,
  });
  return `${header}.${payload}.e2e-signature`;
}

/** A Supabase Session object in the shape auth-js persists to localStorage. */
function buildSession() {
  const expiresAt = Math.floor(Date.now() / 1000) + ONE_YEAR_SECONDS;
  return {
    access_token: fakeJwt(expiresAt),
    refresh_token: 'e2e-refresh-token',
    token_type: 'bearer',
    expires_in: ONE_YEAR_SECONDS,
    expires_at: expiresAt,
    user: {
      id: USER_ID,
      aud: 'authenticated',
      role: 'authenticated',
      email: 'e2e@example.com',
      email_confirmed_at: '2020-01-01T00:00:00Z',
      phone: '',
      confirmed_at: '2020-01-01T00:00:00Z',
      last_sign_in_at: '2020-01-01T00:00:00Z',
      app_metadata: { provider: 'email', providers: ['email'] },
      user_metadata: { display_name: 'E2E User' },
      identities: [],
      created_at: '2020-01-01T00:00:00Z',
      updated_at: '2020-01-01T00:00:00Z',
    },
  };
}

function fulfillJson(route: Route, body: unknown, status = 200): Promise<void> {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
}

/** Stubbed project payload for `fetchProject` (GET project by storyboard id). */
function projectEnvelope(nodes: unknown[] = [], edges: unknown[] = [], characters: unknown[] = []) {
  return {
    success: true,
    data: {
      project: {
        id: STORYBOARD_ID,
        name: 'E2E Storyboard',
        team_id: TEAM_ID,
        project_id: PARENT_PROJECT_ID,
        created_at: '2020-01-01T00:00:00Z',
        updated_at: '2020-01-01T00:00:00Z',
      },
      nodes,
      edges,
      characters,
    },
  };
}

export interface StubOptions {
  /** Backend nodes to seed into the loaded project (default: empty canvas). */
  nodes?: unknown[];
  edges?: unknown[];
  characters?: unknown[];
}

/**
 * Seed an authenticated session and intercept the whole backend.
 * Call once per test BEFORE navigating.
 */
export async function setupStubbedSession(page: Page, opts: StubOptions = {}): Promise<void> {
  const session = buildSession();

  await page.addInitScript(
    ([key, sess, teamId]) => {
      try {
        localStorage.setItem(key as string, JSON.stringify(sess));
        localStorage.setItem('mediahub_selected_team', teamId as string);
        localStorage.setItem('mediahub_personal_team', teamId as string);
      } catch {
        /* localStorage unavailable — nothing we can do */
      }
    },
    [STORAGE_KEY, session, TEAM_ID] as const,
  );

  // ── Catch-alls (registered first → lowest priority; later routes win) ──────
  await page.route('**/auth/v1/**', (route) => fulfillJson(route, session));
  await page.route('**/rest/v1/**', (route) => fulfillJson(route, []));
  await page.route('**/api/v1/**', (route) => fulfillJson(route, { success: true, data: [] }));

  // ── Supabase Auth: getClaims() on an HS256 token falls back to getUser(),
  //    which GETs /auth/v1/user and expects a bare user object (not a session).
  //    Many services (teams, notifications, resources) call getClaims on boot.
  await page.route('**/auth/v1/user*', (route) => fulfillJson(route, session.user));

  // ── AI Library: list endpoints return BARE arrays (no envelope). The chat
  //    panel loads agents on open; an envelope here would crash its render.
  await page.route('**/api/v1/ai-library/agents', (route) => fulfillJson(route, []));
  await page.route('**/api/v1/ai-library/agents/*/sessions*', (route) => fulfillJson(route, []));
  // CommitmentsPanel reads resp.items — needs the {items, count} envelope.
  await page.route('**/api/v1/ai-library/commitments*', (route) =>
    fulfillJson(route, { items: [], count: 0 }),
  );

  // ── Supabase REST: team resolution ─────────────────────────────────────────
  await page.route('**/rest/v1/team_members*', (route) =>
    fulfillJson(route, [{ team_id: TEAM_ID }]),
  );
  await page.route('**/rest/v1/teams*', (route) => {
    const url = route.request().url();
    const accept = route.request().headers()['accept'] ?? '';
    // fetchMyTeams filters non-personal teams → none in this fixture.
    if (url.includes('kind=neq.personal')) return fulfillJson(route, []);
    // fetchPersonalTeam uses .maybeSingle() → PostgREST returns a single object.
    if (accept.includes('pgrst.object')) return fulfillJson(route, TEAM);
    return fulfillJson(route, [TEAM]);
  });

  // ── Storyboard project load (fetchProject) ─────────────────────────────────
  await page.route(`**/api/v1/storyboard/projects/${STORYBOARD_ID}`, (route) =>
    fulfillJson(route, projectEnvelope(opts.nodes ?? [], opts.edges ?? [], opts.characters ?? [])),
  );
}

/**
 * Stub the AI generate + image-split endpoints with deterministic fixtures.
 * Layer on top of {@link setupStubbedSession} for creative-flow tests.
 */
export async function setupGenerationStubs(page: Page): Promise<void> {
  // generateImage reads { success, task_id, image_url } at the top level; an
  // inline image_url short-circuits the polling path.
  await page.route('**/api/v1/storyboard/generate/image', (route) =>
    fulfillJson(route, {
      success: true,
      task_id: 'e2e-gen-task',
      image_url: FIXTURE_IMAGE_URL,
    }),
  );

  // splitImage → fixed SplitImageResult (two frames pointing at the fixture).
  await page.route('**/api/v1/storyboard/projects/*/split-image', (route) =>
    fulfillJson(route, {
      success: true,
      data: {
        source_asset_id: 'e2e-asset',
        rows: 1,
        cols: 2,
        frames: [
          frame(0, 0, 0),
          frame(1, 0, 1),
        ],
      },
    }),
  );

  // Generation submits a job and polls the DBOS workflow status endpoint
  // (httpAiGateway.getGenerateImageJob). Status must be a DBOS string ('SUCCESS')
  // and the image URL lives under output.result.
  await page.route('**/api/v1/workflows/*/status', (route) =>
    fulfillJson(route, {
      workflow_id: 'e2e-gen-task',
      status: 'SUCCESS',
      output: { result: FIXTURE_IMAGE_URL },
      error: null,
    }),
  );
}

/** Backend nodes for the generate flow: one upload (with image) + one
 *  storyboard_gen with frame descriptions (so the Generate button is enabled). */
export const GEN_FLOW_NODES: unknown[] = [
  {
    id: 'upload-1',
    node_type: 'upload',
    position_x: -160,
    position_y: 140,
    width: 280,
    height: 320,
    data_json: {
      displayName: 'Source',
      imageUrl: FIXTURE_IMAGE_URL,
      previewImageUrl: FIXTURE_IMAGE_URL,
      aspectRatio: '1:1',
      sourceFileName: 'frame.png',
    },
  },
  {
    id: 'gen-1',
    node_type: 'storyboard_gen',
    position_x: 220,
    position_y: 140,
    width: 340,
    height: 420,
    data_json: {
      displayName: 'Generator',
      gridRows: 1,
      gridCols: 2,
      frames: [
        { id: 'f1', description: 'A red sunrise over mountains', referenceIndex: null },
        { id: 'f2', description: 'A blue ocean at night', referenceIndex: null },
      ],
      model: 'fal/nano-banana-2',
      size: '2K',
      requestAspectRatio: '1:1',
      imageUrl: null,
      aspectRatio: '1:1',
      isGenerating: false,
    },
  },
];

function frame(index: number, row: number, col: number) {
  return {
    id: `e2e-frame-${index}`,
    asset_id: `e2e-frame-asset-${index}`,
    frame_index: index,
    image_url: FIXTURE_IMAGE_URL,
    preview_url: FIXTURE_IMAGE_URL,
    width: 2,
    height: 2,
    row,
    col,
  };
}
