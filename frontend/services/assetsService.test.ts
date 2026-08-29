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
import { createAsset, fetchAsset, searchAssets } from './assetsService';
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
