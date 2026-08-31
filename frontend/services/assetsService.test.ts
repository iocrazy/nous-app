/**
 * Unit tests for assetsService — the slice of /api/v1/assets the "Save as
 * asset" dialog needs (search, create, detail-for-loadouts).
 *
 * Row shapes mirror `_serialize(with_derived(...))` in
 * `backend/app/repositories/assets_repository.py`: every BIGINT column is a
 * JSON **string**, `scope_id` is null only for system presets, and the derived
 * `readiness` block rides alongside the columns.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { reportApiNetworkFailure } from '../utils/apiConfig';
import { getAuthHeaders } from './parserService';
import {
  attachFile,
  attachFiles,
  createAsset,
  createLink,
  createLoadout,
  deleteAsset,
  deleteLink,
  deleteLoadout,
  duplicateAsset,
  detachFile,
  fetchAsset,
  fetchAssetCounts,
  fetchAssetDetail,
  generateSlot,
  linkProject,
  listAssets,
  listProjectAssets,
  previewGenerateSlot,
  regeneratePrompt,
  searchAssets,
  translatePrompt,
  unlinkProject,
  updateAsset,
  updateLoadout,
} from './assetsService';
import { GeneratedApiError } from './apiEnvelope';

vi.mock('../utils/apiConfig', () => ({
  getApiUrl: () => 'https://api.test',
  reportApiNetworkFailure: vi.fn(),
}));
vi.mock('./parserService', () => ({ getAuthHeaders: vi.fn() }));

const authMock = vi.mocked(getAuthHeaders);
const failoverMock = vi.mocked(reportApiNetworkFailure);

const SCOPE = '727145299382534200';

const ASSET_ROW = {
  id: '727145299382534201',
  scope_id: '727145299382534200',
  asset_type: 'character',
  subtype: null,
  name: 'Lin',
  role_tag: 'supporting',
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
  sort_order: 0,
  created_by: '4a2f0e9c-3d1b-4d4a-9a1e-0f6a6d0f1c22',
  created_at: '2026-08-28T10:00:00+00:00',
  updated_at: '2026-08-28T10:00:00+00:00',
  deleted_at: null,
  readiness: { state: 'draft', missing: ['sheet'] },
  file_counts_by_slot: {},
  project_ids: [],
  loadout_count: 1,
};

const PRESET_ROW = { ...ASSET_ROW, id: '1', scope_id: null, name: 'Preset', is_system_preset: true };

const ERROR_409 = {
  success: false,
  error: {
    code: 'asset_exists',
    detail: 'An asset with this name and type already exists in this scope',
    existing_asset_id: '727145299382534201',
  },
};

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

function calledHeader(spy: ReturnType<typeof stubFetch>, name: string): string | null {
  const [, init] = spy.mock.calls[0] as [string, RequestInit | undefined];
  return new Headers(init?.headers).get(name);
}

beforeEach(() => {
  vi.restoreAllMocks();
  // See generatedService.test.ts: set per test, because a getAuthHeaders that
  // resolves `undefined` produces a request with no auth and no failure.
  authMock.mockResolvedValue({ Authorization: 'Bearer test' });
  failoverMock.mockClear();
});

describe('searchAssets', () => {
  it('GETs /assets with scope_id and the supplied filters', async () => {
    const spy = stubFetch({ success: true, data: [ASSET_ROW, PRESET_ROW] });

    const rows = await searchAssets(SCOPE, { type: 'character', q: 'Li', limit: 20 });

    const url = calledUrl(spy);
    expect(url.pathname).toBe('/api/v1/assets');
    expect(url.searchParams.get('scope_id')).toBe(SCOPE);
    expect(url.searchParams.get('type')).toBe('character');
    expect(url.searchParams.get('q')).toBe('Li');
    expect(url.searchParams.get('limit')).toBe('20');
    expect(rows).toHaveLength(2);
    expect(rows[0].readiness).toEqual({ state: 'draft', missing: ['sheet'] });
    // A system preset carries scope_id === null; the type has to admit it or
    // the dialog cannot list presets at all.
    expect(rows[1].scope_id).toBeNull();
    expect(rows[1].is_system_preset).toBe(true);
  });

  it('omits filters that were not supplied', async () => {
    const spy = stubFetch({ success: true, data: [] });

    await searchAssets(SCOPE);

    const url = calledUrl(spy);
    expect(url.searchParams.has('type')).toBe(false);
    expect(url.searchParams.has('q')).toBe(false);
    expect(url.searchParams.has('limit')).toBe(false);
  });
});

describe('createAsset', () => {
  it('POSTs the create body and reads the 201 envelope', async () => {
    const spy = stubFetch({ success: true, data: ASSET_ROW }, 201);

    const asset = await createAsset(SCOPE, { asset_type: 'character', name: 'Lin' });

    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(new URL(url).pathname).toBe('/api/v1/assets');
    expect(new URL(url).searchParams.get('scope_id')).toBe(SCOPE);
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({
      asset_type: 'character',
      name: 'Lin',
    });
    expect(asset.id).toBe('727145299382534201');
  });

  it('surfaces the 409 duplicate with existing_asset_id intact', async () => {
    stubFetch(ERROR_409, 409);

    const err = await createAsset(SCOPE, {
      asset_type: 'character',
      name: 'Lin',
    }).catch((e) => e);

    expect(err).toBeInstanceOf(GeneratedApiError);
    expect(err.status).toBe(409);
    expect(err.code).toBe('asset_exists');
    // The dialog offers "use the existing one" off this id — losing it would
    // turn a recoverable collision into a dead end.
    expect(err.extra.existing_asset_id).toBe('727145299382534201');
  });
});

describe('fetchAsset', () => {
  it('GETs one asset and exposes its loadouts', async () => {
    const spy = stubFetch({
      success: true,
      data: {
        ...ASSET_ROW,
        files: [],
        links: [],
        linked_by: [],
        loadouts: [
          {
            id: '727145299382534501',
            asset_id: '727145299382534201',
            name: 'Default',
            is_default: true,
            costume_ids: [],
            prop_ids: [],
            prompt_extra: null,
            sort_order: 0,
            created_at: '2026-08-28T10:00:00+00:00',
          },
        ],
      },
    });

    const asset = await fetchAsset(SCOPE, '727145299382534201');

    const url = calledUrl(spy);
    expect(url.pathname).toBe('/api/v1/assets/727145299382534201');
    expect(url.searchParams.get('scope_id')).toBe(SCOPE);
    expect(asset.loadouts).toHaveLength(1);
    expect(asset.loadouts[0].id).toBe('727145299382534501');
    expect(asset.loadouts[0].is_default).toBe(true);
  });

  it('defaults loadouts to [] rather than leaking undefined', async () => {
    stubFetch({ success: true, data: ASSET_ROW });

    const asset = await fetchAsset(SCOPE, '727145299382534201');

    expect(asset.loadouts).toEqual([]);
  });
});

describe('auth headers', () => {
  it('rides on the search GET', async () => {
    const spy = stubFetch({ success: true, data: [] });

    await searchAssets(SCOPE);

    expect(authMock).toHaveBeenCalled();
    expect(calledHeader(spy, 'Authorization')).toBe('Bearer test');
  });

  it('rides on the create POST, alongside the JSON content type', async () => {
    const spy = stubFetch({ success: true, data: ASSET_ROW }, 201);

    await createAsset(SCOPE, { asset_type: 'character', name: 'Lin' });

    expect(calledHeader(spy, 'Authorization')).toBe('Bearer test');
    expect(calledHeader(spy, 'Content-Type')).toBe('application/json');
  });

  it('rides on the detail GET', async () => {
    const spy = stubFetch({ success: true, data: ASSET_ROW });

    await fetchAsset(SCOPE, '727145299382534201');

    expect(calledHeader(spy, 'Authorization')).toBe('Bearer test');
  });

  it('survives getAuthHeaders returning a Headers instance', async () => {
    authMock.mockResolvedValueOnce(new Headers({ Authorization: 'Bearer hdr' }));
    const spy = stubFetch({ success: true, data: ASSET_ROW }, 201);

    await createAsset(SCOPE, { asset_type: 'character', name: 'Lin' });

    expect(calledHeader(spy, 'Authorization')).toBe('Bearer hdr');
    expect(calledHeader(spy, 'Content-Type')).toBe('application/json');
  });
});

describe('failover reporting', () => {
  it('reports a fetch-level failure exactly once', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValueOnce(new TypeError('Failed to fetch'));
    vi.spyOn(console, 'error').mockImplementation(() => {});

    await searchAssets(SCOPE).catch(() => {});

    expect(failoverMock).toHaveBeenCalledTimes(1);
  });

  it('does NOT report the 409 — the origin answered', async () => {
    stubFetch(ERROR_409, 409);

    await createAsset(SCOPE, { asset_type: 'character', name: 'Lin' }).catch(() => {});

    expect(failoverMock).not.toHaveBeenCalled();
  });
});


// ─── P2: the library pages' half of the client ──────────────────────────────
//
// Shapes below are copied from
// `.superpowers/sdd/2026-08-29-asset-library-p2-codex-and-sheets/wire-fixtures-assets.json`,
// which was generated from the Pydantic schemas — string ids, `scope_id` null
// only on presets (CLAUDE.md 边界 mock 必须用真实 JSON 形状).

const ASSET_ID = '727145299382534300';

/** The request as the client actually sent it. */
function callAt(spy: ReturnType<typeof stubFetch>, i = 0): [URL, RequestInit] {
  const [url, init] = spy.mock.calls[i] as [string, RequestInit];
  return [new URL(url), init];
}

function body(init: RequestInit): unknown {
  return JSON.parse(String(init.body));
}

describe('listAssets', () => {
  it('sends every supplied filter, with the backend\'s own param names', async () => {
    const spy = stubFetch({ success: true, data: [ASSET_ROW] });

    await listAssets(SCOPE, {
      type: 'location',
      projectId: '55',
      q: 'grove',
      readiness: 'draft',
      tag: 'hero',
      sort: 'name',
      limit: 20,
      offset: 40,
    });

    const [url] = callAt(spy);
    expect(url.pathname).toBe('/api/v1/assets');
    expect(url.searchParams.get('scope_id')).toBe(SCOPE);
    expect(url.searchParams.get('type')).toBe('location');
    // camelCase on the way in, snake_case on the wire — a mismatch here is an
    // unfiltered shelf that LOOKS filtered, which is why it is asserted.
    expect(url.searchParams.get('project_id')).toBe('55');
    expect(url.searchParams.get('q')).toBe('grove');
    expect(url.searchParams.get('readiness')).toBe('draft');
    expect(url.searchParams.get('tag')).toBe('hero');
    expect(url.searchParams.get('sort')).toBe('name');
    expect(url.searchParams.get('limit')).toBe('20');
    expect(url.searchParams.get('offset')).toBe('40');
  });

  it('omits every filter that was not supplied, so the server defaults apply', async () => {
    const spy = stubFetch({ success: true, data: [] });

    await listAssets(SCOPE);

    const [url] = callAt(spy);
    expect([...url.searchParams.keys()]).toEqual(['scope_id']);
  });

  it('sends offset=0 rather than dropping it', async () => {
    // `0` is falsy; a truthiness check would silently turn "page 1" into
    // "whatever the server defaults to".
    const spy = stubFetch({ success: true, data: [] });

    await listAssets(SCOPE, { offset: 0, limit: 0 });

    const [url] = callAt(spy);
    expect(url.searchParams.get('offset')).toBe('0');
    expect(url.searchParams.get('limit')).toBe('0');
  });

  it('returns [] when the envelope data is not a list', async () => {
    stubFetch({ success: true, data: null });
    await expect(listAssets(SCOPE)).resolves.toEqual([]);
  });
});

describe('listProjectAssets', () => {
  it('uses the project route and sends NO scope_id', async () => {
    const spy = stubFetch({ success: true, data: [ASSET_ROW] });

    await listProjectAssets('55', 'character');

    const [url] = callAt(spy);
    // The scope is derived from the project server-side (a personal project
    // resolves to its OWNER's team). Sending the caller's would be the wrong
    // scope, answered as an empty list that reads like an empty library.
    expect(url.pathname).toBe('/api/v1/projects/55/assets');
    expect(url.searchParams.has('scope_id')).toBe(false);
    expect(url.searchParams.get('type')).toBe('character');
  });

  it('sends no query string at all when no type is asked for', async () => {
    const spy = stubFetch({ success: true, data: [] });
    await listProjectAssets('55');
    const [url] = callAt(spy);
    expect(url.search).toBe('');
  });
});

describe('fetchAssetCounts', () => {
  it('GETs /assets/counts and returns the six tallies', async () => {
    const spy = stubFetch({
      success: true,
      data: { character: 4, location: 2, prop: 0, costume: 0, prompt: 1, audio: 0 },
    });

    const counts = await fetchAssetCounts(SCOPE);

    const [url] = callAt(spy);
    expect(url.pathname).toBe('/api/v1/assets/counts');
    expect(url.searchParams.get('scope_id')).toBe(SCOPE);
    expect(counts.character).toBe(4);
    expect(counts.prop).toBe(0);
  });

  it('surfaces a refusal instead of resolving to a fake tally', async () => {
    stubFetch(
      { success: false, error: { code: 'not_a_member', detail: 'nope' } },
      403,
    );

    const err = await fetchAssetCounts(SCOPE).catch((e) => e);

    expect(err).toBeInstanceOf(GeneratedApiError);
    expect(err.code).toBe('not_a_member');
    expect(err.status).toBe(403);
  });
});

describe('fetchAssetDetail', () => {
  it('GETs the detail row and normalizes its four relation arrays', async () => {
    const spy = stubFetch({ success: true, data: ASSET_ROW });

    const asset = await fetchAssetDetail(SCOPE, ASSET_ID);

    const [url] = callAt(spy);
    expect(url.pathname).toBe(`/api/v1/assets/${ASSET_ID}`);
    // A caller mapping over any of these must not crash on a partial payload.
    expect(asset.files).toEqual([]);
    expect(asset.links).toEqual([]);
    expect(asset.linked_by).toEqual([]);
    expect(asset.loadouts).toEqual([]);
  });

  it('keeps outgoing and incoming links apart', async () => {
    // They answer different questions ("what does this wear" vs "who wears
    // this"); folding one into the other would render a costume's page as if
    // the costume were the wearer.
    stubFetch({
      success: true,
      data: {
        ...ASSET_ROW,
        links: [{ from_asset_id: ASSET_ID, to_asset_id: '9', relation: 'wears', created_at: null }],
        linked_by: [{ from_asset_id: '8', to_asset_id: ASSET_ID, relation: 'holds', created_at: null }],
      },
    });

    const asset = await fetchAssetDetail(SCOPE, ASSET_ID);

    expect(asset.links[0].to_asset_id).toBe('9');
    expect(asset.linked_by[0].from_asset_id).toBe('8');
  });
});

describe('updateAsset', () => {
  it('PATCHes exactly the keys it was given', async () => {
    const spy = stubFetch({ success: true, data: ASSET_ROW });

    await updateAsset(SCOPE, ASSET_ID, { name: 'Sang Yao' });

    const [url, init] = callAt(spy);
    expect(init.method).toBe('PATCH');
    expect(url.searchParams.get('scope_id')).toBe(SCOPE);
    // Only `name`. Every extra key the client volunteers is a field the
    // backend then WRITES — a `{...form}` body would clear whatever the user
    // did not touch.
    expect(body(init)).toEqual({ name: 'Sang Yao' });
  });

  it('sends an explicit null through, because null CLEARS', async () => {
    const spy = stubFetch({ success: true, data: ASSET_ROW });

    await updateAsset(SCOPE, ASSET_ID, { subtype: null });

    expect(body(callAt(spy)[1])).toEqual({ subtype: null });
  });

  it('surfaces the preset 403 rather than pretending the edit landed', async () => {
    stubFetch(
      {
        success: false,
        error: {
          code: 'system_preset_readonly',
          detail: 'System presets are read-only; duplicate to edit',
        },
      },
      403,
    );

    const err = await updateAsset(SCOPE, '1', { name: 'x' }).catch((e) => e);

    expect(err).toBeInstanceOf(GeneratedApiError);
    expect(err.code).toBe('system_preset_readonly');
  });
});

describe('deleteAsset / duplicateAsset', () => {
  it('DELETEs and resolves only on a real 2xx', async () => {
    const spy = stubFetch({ success: true, data: { deleted: true } });

    await deleteAsset(SCOPE, ASSET_ID);

    const [url, init] = callAt(spy);
    expect(init.method).toBe('DELETE');
    expect(url.pathname).toBe(`/api/v1/assets/${ASSET_ID}`);
    // No body → no Content-Type claim about one.
    expect(new Headers(init.headers).get('Content-Type')).toBeNull();
  });

  it('rejects a failed DELETE instead of resolving void', async () => {
    stubFetch({ success: false, error: { code: 'asset_not_found', detail: 'x' } }, 404);
    await expect(deleteAsset(SCOPE, ASSET_ID)).rejects.toBeInstanceOf(GeneratedApiError);
  });

  it('POSTs a duplicate with an empty body when no name is given', async () => {
    const spy = stubFetch({ success: true, data: ASSET_ROW }, 201);

    await duplicateAsset(SCOPE, ASSET_ID);

    const [url, init] = callAt(spy);
    expect(url.pathname).toBe(`/api/v1/assets/${ASSET_ID}/duplicate`);
    // `{}` not `{name: undefined}`: the body forbids unknown keys and the
    // server's own default is "{source} (copy)".
    expect(body(init)).toEqual({});
  });

  it('passes an explicit copy name through', async () => {
    const spy = stubFetch({ success: true, data: ASSET_ROW }, 201);
    await duplicateAsset(SCOPE, ASSET_ID, 'Sang Yao v2');
    expect(body(callAt(spy)[1])).toEqual({ name: 'Sang Yao v2' });
  });

  it('normalizes the copy\'s relation arrays like any other detail row', async () => {
    stubFetch({ success: true, data: ASSET_ROW }, 201);
    const copy = await duplicateAsset(SCOPE, ASSET_ID);
    expect(copy.loadouts).toEqual([]);
  });
});

describe('files', () => {
  const FILE_ROW = {
    asset_id: ASSET_ID,
    resource_id: '727145299382534146',
    slot: 'sheet',
    loadout_id: null,
    sort_order: 0,
    note: null,
    attached_by: null,
    attached_at: '2026-08-29T10:00:00Z',
  };

  it('POSTs one attach as a bare object', async () => {
    const spy = stubFetch({ success: true, data: FILE_ROW }, 201);

    await attachFile(SCOPE, ASSET_ID, { resource_id: '727145299382534146', slot: 'sheet' });

    const [url, init] = callAt(spy);
    expect(url.pathname).toBe(`/api/v1/assets/${ASSET_ID}/files`);
    expect(body(init)).toEqual({ resource_id: '727145299382534146', slot: 'sheet' });
  });

  it('POSTs a batch under `items`, which is what selects the atomic path', async () => {
    const spy = stubFetch({ success: true, data: [FILE_ROW] }, 201);

    await attachFiles(SCOPE, ASSET_ID, [{ resource_id: '1' }, { resource_id: '2' }]);

    expect(body(callAt(spy)[1])).toEqual({
      items: [{ resource_id: '1' }, { resource_id: '2' }],
    });
  });

  it('returns [] when a batch response is not a list', async () => {
    stubFetch({ success: true, data: null }, 201);
    await expect(attachFiles(SCOPE, ASSET_ID, [{ resource_id: '1' }])).resolves.toEqual([]);
  });

  it('encodes the slot into the detach path', async () => {
    const spy = stubFetch({ success: true, data: { detached: true } });

    await detachFile(SCOPE, ASSET_ID, '727145299382534146', 'in scene/extra');

    const [url] = callAt(spy);
    // An un-encoded slash would address a DIFFERENT route entirely.
    expect(url.pathname).toBe(
      `/api/v1/assets/${ASSET_ID}/files/727145299382534146/in%20scene%2Fextra`,
    );
  });
});

describe('links', () => {
  const LINK_ROW = {
    from_asset_id: ASSET_ID,
    to_asset_id: '727145299382534310',
    relation: 'wears',
    created_at: null,
  };

  it('POSTs the relation and the target id', async () => {
    const spy = stubFetch({ success: true, data: LINK_ROW }, 201);

    await createLink(SCOPE, ASSET_ID, '727145299382534310', 'wears');

    const [url, init] = callAt(spy);
    expect(url.pathname).toBe(`/api/v1/assets/${ASSET_ID}/links`);
    expect(body(init)).toEqual({ to_asset_id: '727145299382534310', relation: 'wears' });
  });

  it('DELETEs by (target, relation) — both are part of the key', async () => {
    const spy = stubFetch({ success: true, data: { removed: true } });

    await deleteLink(SCOPE, ASSET_ID, '727145299382534310', 'holds');

    const [url, init] = callAt(spy);
    expect(init.method).toBe('DELETE');
    expect(url.pathname).toBe(
      `/api/v1/assets/${ASSET_ID}/links/727145299382534310/holds`,
    );
  });

  it('surfaces a rejected relation instead of resolving quietly', async () => {
    stubFetch(
      { success: false, error: { code: 'link_not_allowed', detail: 'no' } },
      422,
    );

    const err = await createLink(SCOPE, ASSET_ID, '9', 'wears').catch((e) => e);

    expect(err.code).toBe('link_not_allowed');
  });
});

describe('loadouts', () => {
  const LOADOUT_ROW = {
    id: '727145299382534400',
    asset_id: ASSET_ID,
    name: 'Night raid',
    is_default: false,
    costume_ids: [],
    prop_ids: [],
    prompt_extra: null,
    sort_order: 1,
    created_at: '2026-08-29T10:00:00Z',
  };

  it('POSTs a create', async () => {
    const spy = stubFetch({ success: true, data: LOADOUT_ROW }, 201);

    await createLoadout(SCOPE, ASSET_ID, { name: 'Night raid', costume_ids: ['9'] });

    const [url, init] = callAt(spy);
    expect(url.pathname).toBe(`/api/v1/assets/${ASSET_ID}/loadouts`);
    expect(body(init)).toEqual({ name: 'Night raid', costume_ids: ['9'] });
  });

  it('PATCHes an update at the loadout path', async () => {
    const spy = stubFetch({ success: true, data: LOADOUT_ROW });

    await updateLoadout(SCOPE, ASSET_ID, '727145299382534400', { is_default: true });

    const [url, init] = callAt(spy);
    expect(init.method).toBe('PATCH');
    expect(url.pathname).toBe(
      `/api/v1/assets/${ASSET_ID}/loadouts/727145299382534400`,
    );
    expect(body(init)).toEqual({ is_default: true });
  });

  it('DELETEs one and rejects the refusal to delete the default', async () => {
    stubFetch(
      { success: false, error: { code: 'default_loadout_locked', detail: 'no' } },
      422,
    );

    const err = await deleteLoadout(SCOPE, ASSET_ID, '1').catch((e) => e);

    expect(err).toBeInstanceOf(GeneratedApiError);
    expect(err.code).toBe('default_loadout_locked');
  });
});

describe('prompt AI', () => {
  it('POSTs the translate direction and the force flag', async () => {
    const spy = stubFetch({ success: true, data: ASSET_ROW });

    await translatePrompt(SCOPE, ASSET_ID, 'zh');

    const [url, init] = callAt(spy);
    expect(url.pathname).toBe(`/api/v1/assets/${ASSET_ID}/prompt/translate`);
    // `force` is sent explicitly rather than omitted: "do not overwrite" is a
    // decision the caller made, not a default it is hoping for.
    expect(body(init)).toEqual({ target_lang: 'zh', force: false });
  });

  it('passes force through when the user asked to overwrite', async () => {
    const spy = stubFetch({ success: true, data: ASSET_ROW });
    await translatePrompt(SCOPE, ASSET_ID, 'en', true);
    expect(body(callAt(spy)[1])).toEqual({ target_lang: 'en', force: true });
  });

  it('surfaces the 503 when the translation agent is unreachable', async () => {
    stubFetch(
      { success: false, error: { code: 'translate_unavailable', detail: 'down' } },
      503,
    );

    const err = await translatePrompt(SCOPE, ASSET_ID, 'zh').catch((e) => e);

    // A 503 rendered as success would be a 200 over an unchanged asset.
    expect(err.status).toBe(503);
    expect(err.code).toBe('translate_unavailable');
    expect(failoverMock).not.toHaveBeenCalled();
  });

  it('POSTs regenerate with no body at all', async () => {
    const spy = stubFetch({ success: true, data: ASSET_ROW });

    await regeneratePrompt(SCOPE, ASSET_ID);

    const [url, init] = callAt(spy);
    expect(url.pathname).toBe(`/api/v1/assets/${ASSET_ID}/prompt/regenerate`);
    expect(init.body).toBeUndefined();
    expect(new Headers(init.headers).get('Content-Type')).toBeNull();
  });
});

describe('slot generation', () => {
  it('GETs the preview with slot and loadout on the query string', async () => {
    const spy = stubFetch({
      success: true,
      data: {
        positive: 'same woman as reference',
        negative: 'blurry',
        reference_resource_ids: ['727145299382534146'],
        aspect_ratio: '16:9',
        model: null,
      },
    });

    const preview = await previewGenerateSlot(SCOPE, ASSET_ID, 'stills', '400');

    const [url, init] = callAt(spy);
    expect(url.pathname).toBe(`/api/v1/assets/${ASSET_ID}/generate-slot/preview`);
    expect(url.searchParams.get('slot')).toBe('stills');
    expect(url.searchParams.get('loadout_id')).toBe('400');
    expect(init.method).toBeUndefined(); // a GET, not a POST
    expect(preview.reference_resource_ids).toEqual(['727145299382534146']);
  });

  it('omits loadout_id when there is none', async () => {
    const spy = stubFetch({
      success: true,
      data: { positive: '', negative: '', reference_resource_ids: [], aspect_ratio: '1:1', model: null },
    });

    await previewGenerateSlot(SCOPE, ASSET_ID, 'sheet');

    expect(callAt(spy)[0].searchParams.has('loadout_id')).toBe(false);
  });

  it('reads the 202 off `generation_ids`, and keeps failed / skipped', async () => {
    const spy = stubFetch(
      {
        success: true,
        data: {
          generation_ids: ['727145299382534900', '727145299382534901'],
          failed: [{ index: 2, code: 'provider_error', detail: 'rate limited' }],
          skipped_references: [
            { resource_id: '727145299382534146', reason: 'not materializable' },
          ],
          inbox_state: 'unreviewed',
        },
      },
      202,
    );

    const out = await generateSlot(SCOPE, ASSET_ID, { slot: 'stills', count: 3 });

    const [url, init] = callAt(spy);
    expect(url.pathname).toBe(`/api/v1/assets/${ASSET_ID}/generate-slot`);
    expect(body(init)).toEqual({ slot: 'stills', count: 3 });
    // The field is `generation_ids`, NOT `generations` — reading the wrong one
    // yields undefined, i.e. a successful run rendered as producing nothing.
    expect(out.generation_ids).toHaveLength(2);
    // Both honest-failure channels survive; dropping either reports a partial
    // run as an unqualified success.
    expect(out.failed[0].index).toBe(2);
    expect(out.skipped_references[0].reason).toBe('not materializable');
  });

  it('normalizes the three arrays when the payload omits them', async () => {
    stubFetch({ success: true, data: { inbox_state: 'unreviewed' } }, 202);

    const out = await generateSlot(SCOPE, ASSET_ID, { slot: 'sheet' });

    expect(out.generation_ids).toEqual([]);
    expect(out.failed).toEqual([]);
    expect(out.skipped_references).toEqual([]);
  });

  it('surfaces the all-units-failed 503 rather than an empty success', async () => {
    stubFetch(
      { success: false, error: { code: 'generation_failed', detail: 'provider down' } },
      503,
    );

    const err = await generateSlot(SCOPE, ASSET_ID, { slot: 'sheet' }).catch((e) => e);

    expect(err.code).toBe('generation_failed');
  });
});

describe('project refs', () => {
  it('POSTs the project id', async () => {
    const spy = stubFetch({ success: true, data: { linked: true } }, 201);

    await linkProject(SCOPE, ASSET_ID, '55');

    const [url, init] = callAt(spy);
    expect(url.pathname).toBe(`/api/v1/assets/${ASSET_ID}/project-refs`);
    expect(body(init)).toEqual({ project_id: '55' });
  });

  it('DELETEs by project id in the path', async () => {
    const spy = stubFetch({ success: true, data: { unlinked: true } });

    await unlinkProject(SCOPE, ASSET_ID, '55');

    const [url, init] = callAt(spy);
    expect(init.method).toBe('DELETE');
    expect(url.pathname).toBe(`/api/v1/assets/${ASSET_ID}/project-refs/55`);
  });

  it('surfaces the project guard\'s 403 in this router\'s envelope shape', async () => {
    stubFetch(
      { success: false, error: { code: 'project_forbidden', detail: 'no write' } },
      403,
    );

    const err = await linkProject(SCOPE, ASSET_ID, '55').catch((e) => e);

    expect(err.code).toBe('project_forbidden');
  });
});

describe('scope_id and auth ride on every new call', () => {
  // A call that forgets `?scope_id=` is a 403 `not_a_member` at runtime and a
  // green unit test everywhere else, so the sweep is over ALL of them at once
  // rather than one assertion per describe block above.
  const CALLS: Array<[string, () => Promise<unknown>]> = [
    ['listAssets', () => listAssets(SCOPE)],
    ['fetchAssetCounts', () => fetchAssetCounts(SCOPE)],
    ['fetchAssetDetail', () => fetchAssetDetail(SCOPE, ASSET_ID)],
    ['updateAsset', () => updateAsset(SCOPE, ASSET_ID, { name: 'x' })],
    ['deleteAsset', () => deleteAsset(SCOPE, ASSET_ID)],
    ['duplicateAsset', () => duplicateAsset(SCOPE, ASSET_ID)],
    ['attachFile', () => attachFile(SCOPE, ASSET_ID, { resource_id: '1' })],
    ['attachFiles', () => attachFiles(SCOPE, ASSET_ID, [{ resource_id: '1' }])],
    ['detachFile', () => detachFile(SCOPE, ASSET_ID, '1', 'sheet')],
    ['createLink', () => createLink(SCOPE, ASSET_ID, '9', 'wears')],
    ['deleteLink', () => deleteLink(SCOPE, ASSET_ID, '9', 'wears')],
    ['createLoadout', () => createLoadout(SCOPE, ASSET_ID, { name: 'x' })],
    ['updateLoadout', () => updateLoadout(SCOPE, ASSET_ID, '1', { name: 'x' })],
    ['deleteLoadout', () => deleteLoadout(SCOPE, ASSET_ID, '1')],
    ['translatePrompt', () => translatePrompt(SCOPE, ASSET_ID, 'zh')],
    ['regeneratePrompt', () => regeneratePrompt(SCOPE, ASSET_ID)],
    ['previewGenerateSlot', () => previewGenerateSlot(SCOPE, ASSET_ID, 'sheet')],
    ['generateSlot', () => generateSlot(SCOPE, ASSET_ID, { slot: 'sheet' })],
    ['linkProject', () => linkProject(SCOPE, ASSET_ID, '55')],
    ['unlinkProject', () => unlinkProject(SCOPE, ASSET_ID, '55')],
  ];

  it.each(CALLS)('%s sends scope_id and the auth header', async (_name, call) => {
    const spy = stubFetch({ success: true, data: {} });

    await call();

    const [url] = callAt(spy);
    expect(url.searchParams.get('scope_id')).toBe(SCOPE);
    expect(calledHeader(spy, 'Authorization')).toBe('Bearer test');
  });
});
