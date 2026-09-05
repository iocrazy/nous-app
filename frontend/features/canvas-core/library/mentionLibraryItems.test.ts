// features/canvas-core/library/mentionLibraryItems.test.ts
//
// `⌥` + drop → CHIPS. The three cases that make that sentence falsifiable:
// an asset becomes ONE asset chip carrying the two fields a library row does
// not hold; images become one image chip each, in order, on durable urls; and
// a video is a typed refusal that writes nothing rather than a chip showing
// an empty frame.

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

import { EditorGoneError } from './mentionHandles';
import { mentionLibraryItems, type MentionInserters } from './mentionLibraryItems';
import type { LibraryItem } from './librarySearch';

const SCOPE = '727145299382534100';

const ASSET: LibraryItem = {
  store: 'assets',
  id: '727145299382534300',
  title: 'Cole Bannon',
  thumbUrl: '',
  kind: 'character',
  ready: true,
};
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

/** `GET /assets/{id}` — the real wire shape, every id a string. `sheet` is a
 *  character's real primary slot on both sides, so the file list survives
 *  `primarySlotFileIds`; a made-up slot would silently test the empty case. */
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
      asset_id: ASSET.id, resource_id: '600000000000000001', slot: 'sheet',
      loadout_id: null, sort_order: 0, note: null, attached_by: null,
      attached_at: '2026-09-01T00:00:00Z',
    },
    {
      asset_id: ASSET.id, resource_id: '600000000000000002', slot: 'sheet',
      loadout_id: null, sort_order: 1, note: null, attached_by: null,
      attached_at: '2026-09-01T00:00:00Z',
    },
  ],
  links: [],
  linked_by: [],
  loadouts: [],
};

/** The two spies, plus the `MentionInserters` view to hand the helper.
 *  Split because this repo's vitest typings do not let a bare `vi.fn()` sit
 *  in a field typed as a concrete function — the same friction
 *  `components/chat/Composer.ime.test.tsx` already carries. */
function handle() {
  const insertImage = vi.fn();
  const insertAsset = vi.fn();
  const inserters = { insertImage, insertAsset } as unknown as MentionInserters;
  return { inserters, insertImage, insertAsset };
}

beforeEach(() => {
  importResourceAsCanvasMedia.mockReset().mockResolvedValue({
    url: '/api/v1/generated-media/770000000000000001/file',
    kind: 'image',
    id: '770000000000000001',
  });
  fetchAssetDetail.mockReset().mockResolvedValue(ASSET_DETAIL);
  vi.spyOn(console, 'error').mockImplementation(() => {});
});
afterEach(() => vi.restoreAllMocks());

describe('mentionLibraryItems', () => {
  it('an asset becomes ONE asset chip, carrying what the library row cannot', async () => {
    const h = handle();
    const r = await mentionLibraryItems([ASSET], SCOPE, h.inserters);

    expect(h.insertAsset).toHaveBeenCalledTimes(1);
    expect(h.insertAsset).toHaveBeenCalledWith(
      {
        asset_id: ASSET.id,
        name: 'Cole Bannon',
        asset_type: 'character',
        // Neither of these is on a `LibraryItem`; both come from the detail.
        cover_file_id: '600000000000000001',
        ref_resource_ids: ['600000000000000001', '600000000000000002'],
      },
      // A drop has no pending `@query`, so the editor must delete nothing.
      { consumeMention: false },
    );
    expect(h.insertImage).not.toHaveBeenCalled();
    expect(r).toEqual({ mentioned: 1, failed: [] });
  });

  it('two images become two image chips, on durable urls, IN ORDER', async () => {
    const h = handle();
    const r = await mentionLibraryItems([UPLOAD, GENERATED], SCOPE, h.inserters);

    expect(h.insertImage).toHaveBeenCalledTimes(2);
    expect(h.insertImage.mock.calls.map((c) => c[0])).toEqual([
      // The upload is MINTED — a `/resources/{id}` url is not one the
      // backend's reference bridge accepts.
      { url: '/api/v1/generated-media/770000000000000001/file', alias: 'harbour.png', kind: 'image' },
      { url: '/api/v1/generated-media/800000000000000001/file', alias: 'A wide shot', kind: 'image' },
    ]);
    // EVERY insert opts out of the deletion, not just the first.
    expect(h.insertImage.mock.calls.map((c) => c[1])).toEqual([
      { consumeMention: false },
      { consumeMention: false },
    ]);
    expect(r.mentioned).toBe(2);
    expect(r.failed).toEqual([]);
  });

  it('a video is a TYPED refusal and inserts nothing at all', async () => {
    const h = handle();
    const video = { ...GENERATED, kind: 'video' };
    const r = await mentionLibraryItems([video], SCOPE, h.inserters);

    expect(h.insertImage).not.toHaveBeenCalled();
    expect(h.insertAsset).not.toHaveBeenCalled();
    expect(r).toEqual({ mentioned: 0, failed: [{ item: video, reason: 'not_an_image' }] });
  });

  it('a refusal does not stop the items after it', async () => {
    const h = handle();
    const r = await mentionLibraryItems(
      [{ ...UPLOAD, kind: 'audio' }, GENERATED],
      SCOPE,
      h.inserters,
    );

    expect(h.insertImage).toHaveBeenCalledTimes(1);
    expect(r.mentioned).toBe(1);
    expect(r.failed.map((f) => f.reason)).toEqual(['not_an_image']);
  });

  it('an inserter that THROWS is a failure, not a chip — the phantom-success guard', async () => {
    // `PromptNodeView` registers wrappers that throw when the body editor is
    // not mounted (the surface culls off-viewport cards). Counting the call
    // anyway is what made the panel report "1 inserted", clear the pick, and
    // leave the body untouched. The throw must not escape either: an escaping
    // one rejects the whole run and loses the items that DID land.
    const h = handle();
    h.insertImage.mockImplementation(() => {
      throw new EditorGoneError();
    });
    const r = await mentionLibraryItems([GENERATED], SCOPE, h.inserters);

    expect(r.mentioned).toBe(0);
    expect(r.failed).toEqual([{ item: GENERATED, reason: 'editor_gone' }]);
  });

  it('a throwing inserter reports its own item and no other', async () => {
    // Two items, one editor: the throw is not per-item, so both fail — and
    // both must be REPORTED, or a retry silently omits one.
    const h = handle();
    h.insertAsset.mockImplementation(() => {
      throw new EditorGoneError();
    });
    h.insertImage.mockImplementation(() => {
      throw new EditorGoneError();
    });
    const r = await mentionLibraryItems([ASSET, GENERATED], SCOPE, h.inserters);

    expect(r.mentioned).toBe(0);
    expect(r.failed.map((f) => f.item)).toEqual([ASSET, GENERATED]);
    expect(r.failed.every((f) => f.reason === 'editor_gone')).toBe(true);
  });

  it('a throw that is NOT the registry\'s own error is `insert_failed`, never `editor_gone`', async () => {
    // Only `EditorGoneError` means "the card is unmounted". Any other throw is
    // an inserter defect; labelling it `editor_gone` would send a reader to
    // check the viewport for a bug that lives in the editor.
    const h = handle();
    h.insertImage.mockImplementation(() => {
      throw new TypeError('boom');
    });
    const r = await mentionLibraryItems([GENERATED], SCOPE, h.inserters);

    expect(r.mentioned).toBe(0);
    expect(r.failed).toEqual([{ item: GENERATED, reason: 'insert_failed' }]);
  });

  it('a failed asset detail fetch still mentions — the chip works without it', async () => {
    // `handleMentionAsset`'s rule, kept: the RUN re-fetches the bundle from
    // `asset_id`, so a missing cover costs a thumbnail, not the mention.
    // `ref_resource_ids` stays ABSENT rather than `[]` — the strip renders
    // "not asked" differently from "this asset sends nothing".
    fetchAssetDetail.mockRejectedValue(new Error('boom'));
    const h = handle();
    const r = await mentionLibraryItems([ASSET], SCOPE, h.inserters);

    expect(h.insertAsset).toHaveBeenCalledWith(
      { asset_id: ASSET.id, name: 'Cole Bannon', asset_type: 'character', cover_file_id: null },
      { consumeMention: false },
    );
    expect(h.insertAsset.mock.calls[0][0]).not.toHaveProperty('ref_resource_ids');
    expect(r.mentioned).toBe(1);
  });
});
