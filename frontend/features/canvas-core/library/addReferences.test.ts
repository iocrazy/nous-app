// features/canvas-core/library/addReferences.test.ts
//
// Turning a library pick into `manual_refs`. The three stores mint their urls
// three different ways and all three have to come out durable, because the
// backend's reference bridge accepts exactly two url families.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const importResourceAsCanvasMedia = vi.fn();
const fetchAssetDetail = vi.fn();

vi.mock('../smart/mediaImport', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  importResourceAsCanvasMedia: (...a: unknown[]) => importResourceAsCanvasMedia(...a),
}));
vi.mock('../../../services/assetsService', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  fetchAssetDetail: (...a: unknown[]) => fetchAssetDetail(...a),
}));

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { addReferences, resolveReferenceRefs } from './addReferences';
import type { LibraryItem } from './librarySearch';

const SCOPE = '727145299382534100';

const UPLOAD: LibraryItem = {
  store: 'uploads',
  id: '655000000000000001',
  title: 'harbour.png',
  thumbUrl: '',
  kind: 'image',
};
const GENERATED: LibraryItem = {
  store: 'generated',
  id: '800000000000000001',
  title: 'A wide shot',
  thumbUrl: '',
  kind: 'image',
};
const ASSET: LibraryItem = {
  store: 'assets',
  id: '727145299382534300',
  title: 'Cole Bannon',
  thumbUrl: '',
  kind: 'character',
  ready: true,
};

/**
 * `GET /assets/{id}` — every id a string, `files` present.
 *
 * The slot is `sheet` because that IS a character's primary slot, on both
 * sides (`PRIMARY_SLOT` in `components/assets/assetSlots.ts` and in
 * `backend/app/services/assets/slots.py`). A made-up slot name would make
 * this fixture pass through `primarySlotFileIds` as an asset with no files
 * at all, which is a different case entirely — and the one below it.
 */
const ASSET_DETAIL = {
  id: ASSET.id,
  scope_id: SCOPE,
  asset_type: 'character' as const,
  subtype: null,
  name: 'Cole Bannon',
  role_tag: 'lead',
  description: '',
  attrs: {},
  prompt_positive: null,
  prompt_negative: null,
  prompt_positive_zh: null,
  prompt_negative_zh: null,
  platform_params: {},
  cover_file_id: '600000000000000001',
  source: 'manual',
  duplicated_from: null,
  is_system_preset: false,
  in_library: true,
  tags: {},
  sort_order: 0,
  created_by: null,
  created_at: '2026-09-01T00:00:00Z',
  updated_at: '2026-09-01T00:00:00Z',
  readiness: { state: 'ready' as const, missing: [] },
  file_counts_by_slot: { sheet: 2 },
  project_ids: [],
  loadout_count: 0,
  files: [
    {
      asset_id: ASSET.id,
      resource_id: '600000000000000001',
      slot: 'sheet',
      loadout_id: null,
      sort_order: 0,
      note: null,
      attached_by: null,
      attached_at: '2026-09-01T00:00:00Z',
    },
    {
      asset_id: ASSET.id,
      resource_id: '600000000000000002',
      slot: 'sheet',
      loadout_id: null,
      sort_order: 1,
      note: null,
      attached_by: null,
      attached_at: '2026-09-01T00:00:00Z',
    },
  ],
  links: [],
  linked_by: [],
  loadouts: [],
};

function seed(manualRefs: Array<{ url: string; kind: string }> = []): void {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    kind: 'smart',
    canvasId: '900000000000000001',
    nodes: [
      {
        id: 'p1',
        type: 'prompt',
        position: { x: 0, y: 0 },
        data: {
          body: '',
          provider_slug: '',
          agent_id: null,
          run_status: 'idle',
          resource_refs: [],
          manual_refs: manualRefs,
          gen: { kind: 'image', model: 'doubao-seedream', ratio: '1:1', count: 1 },
        },
      },
    ] as never,
    connections: [],
    selection: [],
  });
}

function refs(): Array<{ url: string }> {
  return (
    (useCanvasCoreStore.getState().nodes.find((n) => (n as { id: string }).id === 'p1') as {
      data: { manual_refs?: Array<{ url: string }> };
    }).data.manual_refs ?? []
  );
}

beforeEach(() => {
  importResourceAsCanvasMedia.mockReset().mockResolvedValue({
    url: '/api/v1/generated-media/770000000000000001/file',
    kind: 'image',
    id: '770000000000000001',
  });
  fetchAssetDetail.mockReset().mockResolvedValue(ASSET_DETAIL);
});
afterEach(() => {
  useCanvasCoreStore.getState().reset();
});

describe('resolveReferenceRefs', () => {
  it('an upload is minted into a durable generated-media url', async () => {
    expect(await resolveReferenceRefs(UPLOAD, SCOPE)).toEqual([
      { url: '/api/v1/generated-media/770000000000000001/file', kind: 'image' },
    ]);
    expect(importResourceAsCanvasMedia).toHaveBeenCalledWith(UPLOAD.id);
  });

  it('a generation is ALREADY durable and costs no round trip', async () => {
    expect(await resolveReferenceRefs(GENERATED, SCOPE)).toEqual([
      { url: '/api/v1/generated-media/800000000000000001/file', kind: 'image' },
    ]);
    expect(importResourceAsCanvasMedia).not.toHaveBeenCalled();
  });

  it('an asset expands to its primary-slot files, in the library sort order', async () => {
    expect(await resolveReferenceRefs(ASSET, SCOPE)).toEqual([
      { url: '/api/v1/resources/600000000000000001/cover', kind: 'image' },
      { url: '/api/v1/resources/600000000000000002/cover', kind: 'image' },
    ]);
  });

  it('a video generation is refused rather than added as a picture', async () => {
    await expect(
      resolveReferenceRefs({ ...GENERATED, kind: 'video' }, SCOPE),
    ).rejects.toMatchObject({ reason: 'not_an_image' });
  });

  it('an asset with no primary-slot file is refused, not added empty', async () => {
    fetchAssetDetail.mockResolvedValue({ ...ASSET_DETAIL, files: [] });
    await expect(resolveReferenceRefs(ASSET, SCOPE)).rejects.toMatchObject({
      reason: 'no_image_file',
    });
  });
});

describe('addReferences', () => {
  it('appends every resolved ref to the node, in pick order', async () => {
    seed();
    const result = await addReferences('p1', [GENERATED, ASSET], SCOPE);
    expect(result).toEqual({ added: 3, skipped: 0, clamped: 0, failed: [] });
    expect(refs().map((r) => r.url)).toEqual([
      '/api/v1/generated-media/800000000000000001/file',
      '/api/v1/resources/600000000000000001/cover',
      '/api/v1/resources/600000000000000002/cover',
    ]);
  });

  it('a url already on the node is skipped, not duplicated', async () => {
    seed([{ url: '/api/v1/generated-media/800000000000000001/file', kind: 'image' }]);
    const result = await addReferences('p1', [GENERATED], SCOPE);
    expect(result).toEqual({ added: 0, skipped: 1, clamped: 0, failed: [] });
    expect(refs()).toHaveLength(1);
  });

  it('one failure is REPORTED and the rest still land', async () => {
    seed();
    fetchAssetDetail.mockRejectedValue(new Error('403'));
    const result = await addReferences('p1', [ASSET, GENERATED], SCOPE);
    expect(result.added).toBe(1);
    expect(result.failed).toEqual([{ item: ASSET, reason: 'mint_failed' }]);
    expect(refs()).toHaveLength(1);
  });

  it('the ceiling counts REFS, not picks — one asset can exceed it on its own', async () => {
    seed();
    // The asset resolves to two refs. An item-level clamp would see one pick,
    // let it through whole, and land both — which is the whole reason the
    // ceiling lives in here rather than in the caller's slice.
    const result = await addReferences('p1', [ASSET], SCOPE, { maxRefs: 1 });
    expect(result).toEqual({ added: 1, skipped: 0, clamped: 1, failed: [] });
    expect(refs().map((r) => r.url)).toEqual([
      '/api/v1/resources/600000000000000001/cover',
    ]);
  });

  it('a duplicate occupies no room, so a later legitimate pick still fits', async () => {
    // One slot free of two. The first pick is already on the node, so it must
    // not spend that slot — otherwise the second is refused and the user is
    // told only about the duplicate.
    seed([{ url: '/api/v1/generated-media/800000000000000001/file', kind: 'image' }]);
    const result = await addReferences('p1', [GENERATED, UPLOAD], SCOPE, { maxRefs: 2 });
    expect(result).toEqual({ added: 1, skipped: 1, clamped: 0, failed: [] });
    expect(refs().map((r) => r.url)).toEqual([
      '/api/v1/generated-media/800000000000000001/file',
      '/api/v1/generated-media/770000000000000001/file',
    ]);
  });

  it('no ceiling is passed by the three-arg callers, and none is applied', async () => {
    seed();
    const result = await addReferences('p1', [ASSET], SCOPE);
    expect(result).toEqual({ added: 2, skipped: 0, clamped: 0, failed: [] });
  });

  it('reads the LIVE node after each await, so a concurrent edit is not clobbered', async () => {
    seed();
    importResourceAsCanvasMedia.mockImplementation(async () => {
      // Something else writes to the node while the mint is in flight.
      useCanvasCoreStore.getState().patchNode('p1', {
        data: { manual_refs: [{ url: '/api/v1/resources/1/cover', kind: 'image' }] },
      });
      return { url: '/api/v1/generated-media/770000000000000001/file', kind: 'image' };
    });
    await addReferences('p1', [UPLOAD], SCOPE);
    expect(refs().map((r) => r.url)).toEqual([
      '/api/v1/resources/1/cover',
      '/api/v1/generated-media/770000000000000001/file',
    ]);
  });
});
