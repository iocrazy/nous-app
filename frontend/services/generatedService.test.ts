/**
 * Unit tests for generatedService — the Generated inbox client.
 *
 * The response bodies below are copied VERBATIM from
 * `.superpowers/sdd/2026-08-29-asset-library-p1-generated-inbox/wire-fixtures.json`,
 * captured from the backend's own Pydantic models. Per CLAUDE.md ("边界 mock
 * 必须用真实 JSON 形状"), hand-writing a prettier shape here would let a
 * type mismatch reach production untested.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { reportApiNetworkFailure } from '../utils/apiConfig';
import { getAuthHeaders } from './parserService';
import {
  batchGenerated,
  cleanupGenerated,
  deleteGeneration,
  fetchGenerated,
  fetchGeneratedCounts,
  fetchGeneratedItem,
  GeneratedApiError,
  saveGeneration,
  saveGenerationAsAsset,
  saveResourceAsAsset,
} from './generatedService';

// Spread the real module rather than replacing it with two exports: a
// whole-module factory silently drops every export it does not list, so the
// day `apiEnvelope` started importing one more of them (`isCallerAbort`) the
// helper became `undefined` and threw INSIDE the catch — turning "reports a
// network failure" red for a reason that had nothing to do with failover.
vi.mock('../utils/apiConfig', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../utils/apiConfig')>()),
  getApiUrl: () => 'https://api.test',
  reportApiNetworkFailure: vi.fn(),
}));
vi.mock('./parserService', () => ({ getAuthHeaders: vi.fn() }));

const authMock = vi.mocked(getAuthHeaders);
const failoverMock = vi.mocked(reportApiNetworkFailure);

const SCOPE = '727145299382534200';

// ── fixtures (verbatim) ─────────────────────────────────────────────────────

const PAGE_BODY = {
  success: true,
  data: {
    items: [
      {
        id: '727145299382534145',
        scope_id: '727145299382534200',
        media_kind: 'image',
        mime: 'image/png',
        prompt: 'Prompt 0. more',
        model: 'gpt-image-2',
        provider: 'openai',
        origin_kind: 'canvas_run',
        canvas_id: '325005725244722',
        node_id: 'n9',
        created_at: '2026-08-28T10:00:00Z',
        promoted_resource_id: null,
        review_state: 'unreviewed',
        source_asset_id: '727145299382534201',
        source: {
          kind: 'canvas_run',
          label: 'EP1 · Storyboard · Canvas',
          canvas_id: '325005725244722',
          node_id: 'n9',
          shot_id: null,
          conversation_id: null,
          deep_link: '/team/727145299382534200/canvas/325005725244722?node=n9',
        },
        title: 'Prompt 0',
      },
    ],
    next_cursor: null,
  },
};

const COUNTS_BODY = {
  success: true,
  data: { unreviewed: 12, saved: 87, in_assets: 41 },
};

const SAVE_BODY = {
  success: true,
  data: {
    id: '727145299382534146',
    scope_id: '727145299382534200',
    media_kind: 'image',
    mime: 'image/png',
    prompt: 'Prompt 1. more',
    model: 'gpt-image-2',
    provider: 'openai',
    origin_kind: 'canvas_run',
    canvas_id: '325005725244722',
    node_id: 'n9',
    created_at: '2026-08-28T10:01:00Z',
    promoted_resource_id: '727145299382534301',
    review_state: 'saved',
    source_asset_id: null,
    source: {
      kind: 'canvas_run',
      label: 'EP1 · Storyboard · Canvas',
      canvas_id: '325005725244722',
      node_id: 'n9',
      shot_id: null,
      conversation_id: null,
      deep_link: '/team/727145299382534200/canvas/325005725244722?node=n9',
    },
    title: 'Prompt 1',
  },
};

const SAVE_AS_ASSET_BODY = {
  success: true,
  data: {
    generation: { ...SAVE_BODY.data, review_state: 'in_assets' },
    asset_id: '727145299382534201',
    resource_id: '727145299382534301',
  },
};

/**
 * `POST /resources/{id}/save-as-asset` — the generation 201 plus `generated_id`
 * (`backend/app/api/resources_assets_router.py::save_resource_as_asset`). The
 * `origin_kind` is `library_upload`: the row the server minted registers a My
 * Uploads file, and calling it a chat upload would mislabel it in the inbox
 * forever.
 */
const SAVE_RESOURCE_AS_ASSET_BODY = {
  success: true,
  data: {
    generation: {
      ...SAVE_BODY.data,
      id: '727145299382534177',
      review_state: 'in_assets',
      origin_kind: 'library_upload',
      promoted_resource_id: '727145299382534301',
    },
    asset_id: '727145299382534201',
    resource_id: '727145299382534301',
    generated_id: '727145299382534177',
  },
};

/** 422 from the same route: a video has no slot that takes it. */
const ERROR_KIND_UNSUPPORTED = {
  success: false,
  error: {
    code: 'resource_kind_unsupported',
    detail: "Only image and audio resources can be saved as an asset; this one is 'video/mp4'",
  },
};

const BATCH_BODY = {
  success: true,
  data: {
    ok: ['727145299382534145'],
    failed: [
      {
        id: '727145299382534146',
        code: 'invalid_slot',
        detail: "Slot 'flat' is not valid for character",
      },
    ],
  },
};

const CLEANUP_BODY = {
  success: true,
  data: {
    dry_run: true,
    count: 3,
    sample: [PAGE_BODY.data.items[0]],
    deleted: 0,
    truncated: false,
  },
};

const ERROR_403 = {
  success: false,
  error: { code: 'not_a_member', detail: 'You are not a member of this scope' },
};

const ERROR_409 = {
  success: false,
  error: {
    code: 'asset_exists',
    detail: 'An asset with this name and type already exists in this scope',
    existing_asset_id: '727145299382534201',
  },
};

// ── helpers ─────────────────────────────────────────────────────────────────

function stubFetch(body: unknown, status = 200) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
    ok: status < 400,
    status,
    json: async () => body,
  } as unknown as Response);
}

function calledUrl(spy: ReturnType<typeof stubFetch>): URL {
  const [url] = spy.mock.calls[0] as [string, ...unknown[]];
  return new URL(url);
}

function calledInit(spy: ReturnType<typeof stubFetch>): RequestInit {
  const [, init] = spy.mock.calls[0] as [string, RequestInit];
  return init;
}

function calledHeader(spy: ReturnType<typeof stubFetch>, name: string): string | null {
  const [, init] = spy.mock.calls[0] as [string, RequestInit | undefined];
  // Read through `Headers` so the assertion holds whichever HeadersInit shape
  // the client passed — the point is what the REQUEST carries, not how it was
  // spelled.
  return new Headers(init?.headers).get(name);
}

beforeEach(() => {
  vi.restoreAllMocks();
  // Set explicitly per test rather than once in the vi.mock factory: the
  // factory runs once, so a reset between tests would leave getAuthHeaders
  // resolving `undefined` — and since spreading/merging `undefined` is silent,
  // every header assertion below would still pass against a request carrying
  // no auth at all.
  authMock.mockResolvedValue({ Authorization: 'Bearer test' });
  failoverMock.mockClear();
});

// ── list ────────────────────────────────────────────────────────────────────

describe('fetchGenerated', () => {
  it('hits /generated with scope_id and parses the fixture page', async () => {
    const spy = stubFetch(PAGE_BODY);

    const page = await fetchGenerated(SCOPE);

    const url = calledUrl(spy);
    expect(url.pathname).toBe('/api/v1/generated');
    expect(url.searchParams.get('scope_id')).toBe(SCOPE);
    expect(page.items).toHaveLength(1);
    expect(page.items[0].id).toBe('727145299382534145');
    expect(page.items[0].source.deep_link).toBe(
      '/team/727145299382534200/canvas/325005725244722?node=n9',
    );
    expect(page.items[0].promoted_resource_id).toBeNull();
    expect(page.next_cursor).toBeNull();
  });

  it('repeats origin_kind once per kind rather than joining them', async () => {
    const spy = stubFetch(PAGE_BODY);

    await fetchGenerated(SCOPE, { originKinds: ['canvas_run', 'chat_upload'] });

    const url = calledUrl(spy);
    expect(url.searchParams.getAll('origin_kind')).toEqual(['canvas_run', 'chat_upload']);
  });

  it('sends state=all verbatim (the router alias for "no filter")', async () => {
    const spy = stubFetch(PAGE_BODY);

    await fetchGenerated(SCOPE, { state: 'all' });

    expect(calledUrl(spy).searchParams.get('state')).toBe('all');
  });

  it('omits state and every filter that was not supplied', async () => {
    const spy = stubFetch(PAGE_BODY);

    await fetchGenerated(SCOPE);

    const url = calledUrl(spy);
    expect(url.searchParams.has('state')).toBe(false);
    expect(url.searchParams.has('origin_kind')).toBe(false);
    expect(url.searchParams.has('project_id')).toBe(false);
    expect(url.searchParams.has('media_kind')).toBe(false);
    expect(url.searchParams.has('model')).toBe(false);
    expect(url.searchParams.has('since')).toBe(false);
    expect(url.searchParams.has('source_asset_id')).toBe(false);
    expect(url.searchParams.has('cursor')).toBe(false);
    expect(url.searchParams.has('limit')).toBe(false);
  });

  it('passes the remaining filters through under their wire names', async () => {
    const spy = stubFetch(PAGE_BODY);

    await fetchGenerated(SCOPE, {
      state: 'saved',
      projectId: '325005725244722',
      mediaKind: 'image',
      model: 'gpt-image-2',
      since: '2026-08-01T00:00:00Z',
      sourceAssetId: '727145299382534300',
      cursor: 'abc123',
      limit: 24,
    });

    const url = calledUrl(spy);
    expect(url.searchParams.get('state')).toBe('saved');
    expect(url.searchParams.get('project_id')).toBe('325005725244722');
    expect(url.searchParams.get('media_kind')).toBe('image');
    expect(url.searchParams.get('model')).toBe('gpt-image-2');
    expect(url.searchParams.get('since')).toBe('2026-08-01T00:00:00Z');
    expect(url.searchParams.get('source_asset_id')).toBe('727145299382534300');
    expect(url.searchParams.get('cursor')).toBe('abc123');
    expect(url.searchParams.get('limit')).toBe('24');
  });

  it('sends canvas_id when asked, and omits it otherwise', async () => {
    const spy = stubFetch(PAGE_BODY);
    stubFetch(PAGE_BODY); // the second call needs its own queued response

    await fetchGenerated(SCOPE, { canvasId: '900000000000000001' });
    await fetchGenerated(SCOPE, {});

    const asked = new URL((spy.mock.calls[0] as [string])[0]);
    const omitted = new URL((spy.mock.calls[1] as [string])[0]);
    expect(asked.searchParams.get('canvas_id')).toBe('900000000000000001');
    expect(omitted.searchParams.has('canvas_id')).toBe(false);
  });
});

// ── counts ──────────────────────────────────────────────────────────────────

describe('fetchGeneratedCounts', () => {
  it('hits /generated/counts and returns the three tab counters', async () => {
    const spy = stubFetch(COUNTS_BODY);

    const counts = await fetchGeneratedCounts(SCOPE);

    expect(calledUrl(spy).pathname).toBe('/api/v1/generated/counts');
    expect(calledUrl(spy).searchParams.get('scope_id')).toBe(SCOPE);
    expect(counts).toEqual({ unreviewed: 12, saved: 87, in_assets: 41 });
  });
});

// ── mutations ───────────────────────────────────────────────────────────────

describe('saveGeneration', () => {
  it('POSTs to /generated/{id}/save and returns the updated item', async () => {
    const spy = stubFetch(SAVE_BODY);

    const item = await saveGeneration(SCOPE, '727145299382534146');

    const url = calledUrl(spy);
    expect(url.pathname).toBe('/api/v1/generated/727145299382534146/save');
    expect(url.searchParams.get('scope_id')).toBe(SCOPE);
    expect(calledInit(spy).method).toBe('POST');
    expect(item.review_state).toBe('saved');
    expect(item.promoted_resource_id).toBe('727145299382534301');
  });
});

describe('saveGenerationAsAsset', () => {
  it('POSTs the body and reads the 201 envelope', async () => {
    const spy = stubFetch(SAVE_AS_ASSET_BODY, 201);

    const out = await saveGenerationAsAsset(SCOPE, '727145299382534145', {
      new_asset: { asset_type: 'character', name: 'Lin' },
      slot: 'sheet',
    });

    const url = calledUrl(spy);
    expect(url.pathname).toBe('/api/v1/generated/727145299382534145/save-as-asset');
    const init = calledInit(spy);
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({
      new_asset: { asset_type: 'character', name: 'Lin' },
      slot: 'sheet',
    });
    expect(out.asset_id).toBe('727145299382534201');
    expect(out.resource_id).toBe('727145299382534301');
    expect(out.generation.review_state).toBe('in_assets');
  });
});

describe('saveResourceAsAsset', () => {
  const RESOURCE_ID = '727145299382534301';

  it('POSTs to the RESOURCES route with the same body the generation route takes', async () => {
    const spy = stubFetch(SAVE_RESOURCE_AS_ASSET_BODY, 201);

    await saveResourceAsAsset(SCOPE, RESOURCE_ID, {
      asset_id: '727145299382534201',
      slot: 'sheet',
    });

    const url = calledUrl(spy);
    // NOT `/generated/...`: the path parameter is a resource, and a My Uploads
    // file usually has no `generated_media` row at all.
    expect(url.pathname).toBe(`/api/v1/resources/${RESOURCE_ID}/save-as-asset`);
    expect(url.searchParams.get('scope_id')).toBe(SCOPE);

    const init = calledInit(spy);
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({
      asset_id: '727145299382534201',
      slot: 'sheet',
    });
    expect(calledHeader(spy, 'Authorization')).toBe('Bearer test');
    expect(calledHeader(spy, 'Content-Type')).toBe('application/json');
  });

  it('reads the extra generated_id the generation route does not return', async () => {
    stubFetch(SAVE_RESOURCE_AS_ASSET_BODY, 201);

    const out = await saveResourceAsAsset(SCOPE, RESOURCE_ID, {
      asset_id: '727145299382534201',
    });

    // Strings, not numbers: these are Snowflakes.
    expect(out.generated_id).toBe('727145299382534177');
    expect(out.asset_id).toBe('727145299382534201');
    expect(out.resource_id).toBe(RESOURCE_ID);
    expect(out.generation.review_state).toBe('in_assets');
    expect(out.generation.origin_kind).toBe('library_upload');
  });

  it('surfaces the typed 422 rather than resolving with nothing', async () => {
    stubFetch(ERROR_KIND_UNSUPPORTED, 422);

    const err = await saveResourceAsAsset(SCOPE, RESOURCE_ID, {
      asset_id: '727145299382534201',
    }).catch((e) => e);

    expect(err).toBeInstanceOf(GeneratedApiError);
    expect(err.code).toBe('resource_kind_unsupported');
    expect(err.status).toBe(422);
    // The detail names what IS accepted — the dialog's copy is generic, and
    // this is what a developer reading a log has to go on.
    expect(err.detail).toContain('image');
    expect(err.detail).toContain('audio');
  });

  it('passes the abort signal through and rethrows the cancel untouched', async () => {
    const controller = new AbortController();
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementationOnce(async (_u, init) => {
      // The signal must reach fetch: without it a cancelled dialog keeps the
      // request in flight and the late reply lands on a closed dialog.
      expect((init as RequestInit).signal).toBe(controller.signal);
      throw Object.assign(new Error('aborted'), { name: 'AbortError' });
    });

    const err = await saveResourceAsAsset(
      SCOPE,
      RESOURCE_ID,
      { asset_id: '727145299382534201' },
      { signal: controller.signal },
    ).catch((e) => e);

    expect(spy).toHaveBeenCalledTimes(1);
    // A caller's own cancel is NOT evidence the server is down, so it must not
    // arrive wrapped (and must not feed the failover counter).
    expect(err).not.toBeInstanceOf(GeneratedApiError);
    expect((err as Error).name).toBe('AbortError');
    expect(failoverMock).not.toHaveBeenCalled();
  });
});

describe('deleteGeneration', () => {
  it('DELETEs /generated/{id} with the scope', async () => {
    const spy = stubFetch({ success: true, data: { deleted: true } });

    await deleteGeneration(SCOPE, '727145299382534145');

    const url = calledUrl(spy);
    expect(url.pathname).toBe('/api/v1/generated/727145299382534145');
    expect(url.searchParams.get('scope_id')).toBe(SCOPE);
    expect(calledInit(spy).method).toBe('DELETE');
  });
});

describe('batchGenerated', () => {
  it('reports per-id outcomes without collapsing partial failure', async () => {
    const spy = stubFetch(BATCH_BODY);

    const out = await batchGenerated(SCOPE, {
      ids: ['727145299382534145', '727145299382534146'],
      action: 'save',
    });

    expect(calledUrl(spy).pathname).toBe('/api/v1/generated/batch');
    expect(out.ok).toEqual(['727145299382534145']);
    expect(out.failed).toHaveLength(1);
    expect(out.failed[0].code).toBe('invalid_slot');
  });
});

describe('cleanupGenerated', () => {
  it('POSTs the cleanup body and parses the dry-run preview', async () => {
    const spy = stubFetch(CLEANUP_BODY);

    const out = await cleanupGenerated(SCOPE, { older_than_days: 30, dry_run: true });

    expect(calledUrl(spy).pathname).toBe('/api/v1/generated/cleanup');
    expect(JSON.parse(String(calledInit(spy).body))).toEqual({
      older_than_days: 30,
      dry_run: true,
    });
    expect(out.dry_run).toBe(true);
    expect(out.count).toBe(3);
    expect(out.deleted).toBe(0);
    expect(out.truncated).toBe(false);
    expect(out.sample[0].id).toBe('727145299382534145');
  });
});

// ── errors ──────────────────────────────────────────────────────────────────

describe('GeneratedApiError', () => {
  it('parses the router error envelope (403)', async () => {
    stubFetch(ERROR_403, 403);

    const err = await fetchGenerated(SCOPE).catch((e) => e);

    expect(err).toBeInstanceOf(GeneratedApiError);
    expect(err.status).toBe(403);
    expect(err.code).toBe('not_a_member');
    expect(err.detail).toBe('You are not a member of this scope');
  });

  it('keeps the envelope extras (409 existing_asset_id)', async () => {
    stubFetch(ERROR_409, 409);

    const err = await saveGenerationAsAsset(SCOPE, '1', {
      new_asset: { asset_type: 'character', name: 'Lin' },
    }).catch((e) => e);

    expect(err).toBeInstanceOf(GeneratedApiError);
    expect(err.status).toBe(409);
    expect(err.code).toBe('asset_exists');
    expect(err.extra.existing_asset_id).toBe('727145299382534201');
  });

  it('falls back to http_<status> when the body is not an envelope', async () => {
    stubFetch({ detail: 'Internal Server Error' }, 500);

    const err = await fetchGeneratedCounts(SCOPE).catch((e) => e);

    expect(err).toBeInstanceOf(GeneratedApiError);
    expect(err.status).toBe(500);
    expect(err.code).toBe('http_500');
  });

  it('falls back to http_<status> when the body is not even JSON', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: false,
      status: 502,
      json: async () => {
        throw new SyntaxError('Unexpected token < in JSON');
      },
    } as unknown as Response);

    const err = await fetchGeneratedCounts(SCOPE).catch((e) => e);

    expect(err).toBeInstanceOf(GeneratedApiError);
    expect(err.status).toBe(502);
    expect(err.code).toBe('http_502');
  });

  it('normalizes a fetch-level failure instead of leaking a TypeError', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValueOnce(new TypeError('Failed to fetch'));
    vi.spyOn(console, 'error').mockImplementation(() => {});

    const err = await fetchGeneratedCounts(SCOPE).catch((e) => e);

    expect(err).toBeInstanceOf(GeneratedApiError);
    expect(err.code).toBe('network');
    expect(err.status).toBe(0);
  });
});

// ── auth headers ────────────────────────────────────────────────────────────

describe('auth headers', () => {
  it('rides on a GET', async () => {
    const spy = stubFetch(PAGE_BODY);

    await fetchGenerated(SCOPE);

    expect(authMock).toHaveBeenCalled();
    expect(calledHeader(spy, 'Authorization')).toBe('Bearer test');
  });

  it('rides on a POST, alongside the JSON content type', async () => {
    const spy = stubFetch(CLEANUP_BODY);

    await cleanupGenerated(SCOPE, { dry_run: true });

    expect(calledHeader(spy, 'Authorization')).toBe('Bearer test');
    expect(calledHeader(spy, 'Content-Type')).toBe('application/json');
  });

  it('rides on a bodyless POST', async () => {
    const spy = stubFetch(SAVE_BODY);

    await saveGeneration(SCOPE, '727145299382534146');

    expect(calledHeader(spy, 'Authorization')).toBe('Bearer test');
  });

  it('survives getAuthHeaders returning a Headers instance', async () => {
    // `getAuthHeaders` is typed `Promise<HeadersInit>`, which admits this.
    // Object-spreading a Headers yields {} — every request would 401 and no
    // type error would be raised, so the shape is pinned here.
    authMock.mockResolvedValueOnce(new Headers({ Authorization: 'Bearer hdr' }));
    const spy = stubFetch(CLEANUP_BODY);

    await cleanupGenerated(SCOPE, { dry_run: true });

    expect(calledHeader(spy, 'Authorization')).toBe('Bearer hdr');
    expect(calledHeader(spy, 'Content-Type')).toBe('application/json');
  });
});

// ── dual-channel failover signal ────────────────────────────────────────────

describe('failover reporting', () => {
  it('reports a fetch-level failure exactly once', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValueOnce(new TypeError('Failed to fetch'));
    vi.spyOn(console, 'error').mockImplementation(() => {});

    await fetchGeneratedCounts(SCOPE).catch(() => {});

    expect(failoverMock).toHaveBeenCalledTimes(1);
  });

  it('does NOT report an HTTP error — the origin answered', async () => {
    stubFetch({ detail: 'Internal Server Error' }, 500);

    await fetchGeneratedCounts(SCOPE).catch(() => {});

    expect(failoverMock).not.toHaveBeenCalled();
  });

  it('does NOT report an envelope refusal either', async () => {
    stubFetch(ERROR_403, 403);

    await fetchGenerated(SCOPE).catch(() => {});

    expect(failoverMock).not.toHaveBeenCalled();
  });
});

// ── one item by id (P4 Task 6) ──────────────────────────────────────────────
//
// The canvas's "As Asset…" reads the row behind an output image so
// `SaveAsAssetDialog` gets a real `GeneratedItem` — including the
// `source_asset_id` its prefill depends on, which the canvas node never held.
// The body below is the SAME shape the list returns, because the router
// decorates both through one code path.

describe('fetchGeneratedItem', () => {
  it('reads /generated/{id} with the scope and returns the decorated row', async () => {
    const spy = stubFetch({ success: true, data: PAGE_BODY.data.items[0] });
    const item = await fetchGeneratedItem(SCOPE, '727145299382534145');
    expect(item.id).toBe('727145299382534145');
    expect(item.source_asset_id).toBe('727145299382534201');
    expect(item.source.deep_link).toContain('?node=n9');
    expect(item.title).toBe('Prompt 0');
    const url = calledUrl(spy);
    expect(url.pathname).toBe('/api/v1/generated/727145299382534145');
    expect(url.searchParams.get('scope_id')).toBe(SCOPE);
  });

  it('carries the auth header', async () => {
    const spy = stubFetch({ success: true, data: PAGE_BODY.data.items[0] });
    await fetchGeneratedItem(SCOPE, '727145299382534145');
    expect(calledHeader(spy, 'Authorization')).toBe('Bearer test');
  });

  it('a row in another scope throws the typed 404, never an empty item', async () => {
    stubFetch(
      { success: false, error: { code: 'generation_not_found', detail: 'nope' } },
      404,
    );
    await expect(fetchGeneratedItem(SCOPE, '1')).rejects.toMatchObject({
      code: 'generation_not_found',
      status: 404,
    });
  });
});
