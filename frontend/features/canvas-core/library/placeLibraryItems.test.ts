// Library rows → canvas nodes.
//
// The DETAIL fixture is the real wire shape of `GET /api/v1/assets/{id}`,
// including the part that matters most here: a character's primary slot is
// `sheet` (see `components/assets/assetSlots.ts`), so a fixture filed under
// any other slot would make the "seeded from its detail" case pass for the
// wrong reason — the card would reference nothing and the assertion would
// still be about an empty list.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const fetchAssetDetail = vi.fn();
const importResourceAsCanvasMedia = vi.fn();

vi.mock('../../../services/assetsService', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  fetchAssetDetail: (...a: unknown[]) => fetchAssetDetail(...a),
}));
vi.mock('../smart/mediaImport', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  importResourceAsCanvasMedia: (...a: unknown[]) => importResourceAsCanvasMedia(...a),
}));

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { placeLibraryItems } from './placeLibraryItems';
import type { LibraryItem } from './librarySearch';

const SCOPE = '727145299382534100';
const ASSET_ID = '727145299382534300';

const ASSET: LibraryItem = {
  store: 'assets', id: ASSET_ID, title: 'Cole Bannon', thumbUrl: '', kind: 'character', ready: true,
};
const GENERATED: LibraryItem = {
  store: 'generated', id: '800000000000000001', title: 'A wide shot', thumbUrl: '', kind: 'image',
};
const UPLOAD: LibraryItem = {
  store: 'uploads', id: '655000000000000001', title: 'harbour.png', thumbUrl: '', kind: 'image',
};
const GENERATED_VIDEO: LibraryItem = {
  store: 'generated', id: '800000000000000002', title: 'A dolly in', thumbUrl: '', kind: 'video',
};

const DETAIL = {
  id: ASSET_ID, scope_id: SCOPE, asset_type: 'character' as const, subtype: null,
  name: 'Cole Bannon', role_tag: 'lead', description: '', attrs: {},
  prompt_positive: null, prompt_negative: null, prompt_positive_zh: null, prompt_negative_zh: null,
  platform_params: {}, cover_file_id: '600000000000000001', source: 'manual',
  duplicated_from: null, is_system_preset: false, in_library: true, tags: {}, sort_order: 0,
  created_by: null, created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z',
  readiness: { state: 'ready' as const, missing: [] },
  file_counts_by_slot: { sheet: 1 }, project_ids: [], loadout_count: 0,
  files: [{
    asset_id: ASSET_ID, resource_id: '600000000000000001', slot: 'sheet',
    loadout_id: null, sort_order: 0, note: null, attached_by: null,
    attached_at: '2026-09-01T00:00:00Z',
  }],
  links: [], linked_by: [], loadouts: [],
};

function seed(nodes: unknown[] = []): void {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    kind: 'smart', canvasId: '900000000000000001',
    nodes: nodes as never, connections: [], selection: [],
  });
}
const nodes = () => useCanvasCoreStore.getState().nodes as Array<{ id: string; type: string; data: Record<string, unknown> }>;

beforeEach(() => {
  fetchAssetDetail.mockReset().mockResolvedValue(DETAIL);
  importResourceAsCanvasMedia.mockReset().mockResolvedValue({
    url: '/api/v1/generated-media/770000000000000001/file', kind: 'image', id: '770000000000000001',
  });
  seed();
});
afterEach(() => useCanvasCoreStore.getState().reset());

describe('placeLibraryItems', () => {
  it('an asset becomes an asset card SEEDED FROM ITS DETAIL, not from the list row', async () => {
    const r = await placeLibraryItems([ASSET], SCOPE, { x: 100, y: 40 });
    expect(r.inserted).toBe(1);
    const card = nodes().find((n) => n.type === 'asset')!;
    expect(card.data.asset_id).toBe(ASSET_ID);
    // The list endpoint answers `file_counts_by_slot`, a tally — a card seeded
    // from it would reference nothing while looking like it worked.
    expect(card.data.selected_file_ids).toEqual(['600000000000000001']);
    expect(fetchAssetDetail).toHaveBeenCalledWith(SCOPE, ASSET_ID);
  });

  it('an asset already on the board is skipped, not duplicated', async () => {
    seed([{ id: 'a1', type: 'asset', position: { x: 0, y: 0 }, data: { asset_id: ASSET_ID, loadout_id: null, selected_file_ids: [], name: 'Cole Bannon', asset_type: 'character', cover_file_id: null, readiness_state: 'ready' } }]);
    const r = await placeLibraryItems([ASSET], SCOPE, { x: 0, y: 0 });
    expect(r).toMatchObject({ inserted: 0, skipped: 1 });
    expect(nodes().filter((n) => n.type === 'asset')).toHaveLength(1);
  });

  it('uploads and generations land in ONE media card holding durable urls', async () => {
    const r = await placeLibraryItems([UPLOAD, GENERATED], SCOPE, { x: 10, y: 20 });
    expect(r.inserted).toBe(2);
    const media = nodes().filter((n) => n.type === 'media');
    expect(media).toHaveLength(1);
    expect((media[0].data.items as Array<{ url: string }>).map((i) => i.url)).toEqual([
      '/api/v1/generated-media/770000000000000001/file',
      '/api/v1/generated-media/800000000000000001/file',
    ]);
  });

  it('a generated VIDEO lands in the card as a video, not refused as a picture', async () => {
    // A media card can hold a video; a reference cannot. Before this, Place on
    // Canvas answered a typed `not_an_image` for every video in the library —
    // loud, but wrong, and it made half the Generated shelf unplaceable.
    const r = await placeLibraryItems([GENERATED_VIDEO], SCOPE, { x: 0, y: 0 });
    expect(r).toMatchObject({ inserted: 1, failed: [] });
    const media = nodes().find((n) => n.type === 'media')!;
    expect(media.data.items).toEqual([
      { url: '/api/v1/generated-media/800000000000000002/file', kind: 'video' },
    ]);
    // One video, so the card is a Video rather than the plural Group.
    expect(media.data.title).toBe('Video');
  });

  it('an audio upload is still refused — a media card renders none of them', async () => {
    const r = await placeLibraryItems([{ ...UPLOAD, kind: 'audio' }], SCOPE, { x: 0, y: 0 });
    expect(r.inserted).toBe(0);
    expect(r.failed).toEqual([{ item: { ...UPLOAD, kind: 'audio' }, reason: 'not_an_image' }]);
  });

  it('the media card is CENTRED on the drop point, not hung off it', async () => {
    // The caller hands in the point the user is looking at; a card placed with
    // its top-left there sits half a card off to one side.
    await placeLibraryItems([GENERATED], SCOPE, { x: 1000, y: 600 });
    const media = nodes().find((n) => n.type === 'media') as unknown as {
      position: { x: number; y: number };
    };
    expect(media.position.x).toBe(1000 - 240 / 2);
    expect(media.position.y).toBe(600 - 60);
  });

  it('mixed stores produce both an asset lane and a media card, in one write', async () => {
    const before = useCanvasCoreStore.getState().revision;
    await placeLibraryItems([ASSET, GENERATED], SCOPE, { x: 0, y: 0 });
    expect(nodes().map((n) => n.type).sort()).toEqual(['asset', 'media']);
    // One setNodes, not two — a half-placed board must never be observable.
    expect(useCanvasCoreStore.getState().revision).toBe(before + 1);
  });

  it('a failed item is reported and the rest still land', async () => {
    importResourceAsCanvasMedia.mockRejectedValue(new Error('boom'));
    const r = await placeLibraryItems([UPLOAD, GENERATED], SCOPE, { x: 0, y: 0 });
    expect(r.inserted).toBe(1);
    expect(r.failed).toEqual([{ item: UPLOAD, reason: 'mint_failed' }]);
  });

  it('with no drop point the assets fall into project lanes instead', async () => {
    await placeLibraryItems([ASSET], SCOPE, null);
    expect(nodes().find((n) => n.type === 'asset')).toBeDefined();
  });

  it('the placed nodes end up selected', async () => {
    await placeLibraryItems([GENERATED], SCOPE, { x: 0, y: 0 });
    expect(useCanvasCoreStore.getState().selection).toEqual([nodes()[0].id]);
  });
});
