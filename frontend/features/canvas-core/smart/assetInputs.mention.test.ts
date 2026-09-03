// features/canvas-core/smart/assetInputs.mention.test.ts
//
// What an `@`-MENTIONED asset hands the prompt that named it.
//
// The ruling is "delivered like an upstream asset card", so the interesting
// assertions are all about SAMENESS: same bundle endpoint, same prefix
// handling, same dropped ledger. The three places they differ are deliberate
// and each has a case here — the checklist is seeded from the asset's primary
// slot (there is no card to tick), mentions come after wired cards in the
// reference order, and the outcome is reported on the prompt rather than a
// card.
//
// Wire shapes throughout: `GET /assets/{id}` answers string ids and
// `asset_files` rows with `slot` / `loadout_id` / `sort_order`; the bundle
// answers `{resource_id, reason}` objects and echoes `max_refs`.

import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { CanvasConnection, CanvasNode } from '../types';
import { resolveAssetInputs } from './promptInputs';
import { resolveAssetRef } from './assetRef';
import type {
  AssetBundle,
  AssetFileRow,
  AssetRowDetail,
} from '../../../services/assetsService';
import type { MentionedAsset } from './mentionedAssets';

const SCOPE = '727145299382534100';
const AVA_ID = '727145299382534201';
const ALLEY_ID = '727145299382534202';

const AVA: MentionedAsset = {
  asset_id: AVA_ID,
  name: 'Ava',
  asset_type: 'character',
  cover_file_id: '727145299382534301',
};
const ALLEY: MentionedAsset = {
  asset_id: ALLEY_ID,
  name: 'Back Alley',
  asset_type: 'location',
  cover_file_id: null,
};

const node = (id: string, type: string, data: Record<string, unknown> = {}): CanvasNode =>
  ({ id, type, position: { x: 0, y: 0 }, data }) as unknown as CanvasNode;

const promptNode = (mentions: MentionedAsset[]): CanvasNode =>
  node('p1', 'prompt', { body: 'x', mentioned_assets: mentions });

const assetNode = (id: string, assetId: string): CanvasNode =>
  node(id, 'asset', {
    asset_id: assetId,
    loadout_id: null,
    selected_file_ids: ['900'],
    name: 'Wired',
    asset_type: 'character',
    cover_file_id: null,
    readiness_state: 'ready',
  });

const edge = (source: string, target: string): CanvasConnection =>
  ({ id: `${source}->${target}`, source, target }) as unknown as CanvasConnection;

const file = (over: Partial<AssetFileRow>): AssetFileRow => ({
  asset_id: AVA_ID,
  resource_id: '1',
  slot: 'sheet',
  loadout_id: null,
  sort_order: 0,
  note: null,
  attached_by: null,
  attached_at: '2026-09-01T00:00:00Z',
  ...over,
});

const detailRow = (over: Partial<AssetRowDetail> = {}): AssetRowDetail =>
  ({
    id: AVA_ID,
    scope_id: SCOPE,
    asset_type: 'character',
    subtype: null,
    name: 'Ava',
    role_tag: 'lead',
    description: '',
    attrs: {},
    prompt_positive: null,
    prompt_negative: null,
    prompt_positive_zh: null,
    prompt_negative_zh: null,
    platform_params: {},
    cover_file_id: '727145299382534301',
    source: 'manual',
    duplicated_from: null,
    is_system_preset: false,
    in_library: true,
    files: [],
    links: [],
    linked_by: [],
    loadouts: [],
    ...over,
  }) as unknown as AssetRowDetail;

const bundle = (over: Partial<AssetBundle> = {}): AssetBundle => ({
  prompt: { positive: '', negative: '' },
  reference_resource_ids: [],
  dropped: [],
  max_refs: 9,
  ...over,
});

let fetchBundle: ReturnType<typeof vi.fn>;
let fetchDetail: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchBundle = vi.fn().mockResolvedValue(bundle());
  fetchDetail = vi.fn().mockResolvedValue(detailRow());
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

const run = (nodes: CanvasNode[], conns: CanvasConnection[] = [], model = 'codex') =>
  resolveAssetInputs('p1', nodes, conns, {
    model,
    scopeId: SCOPE,
    fetch: fetchBundle as never,
    fetchDetail: fetchDetail as never,
  });

describe('a mentioned asset is bundled like a wired card', () => {
  it('sends the PRIMARY slot’s files as the checklist, no loadout', async () => {
    fetchDetail.mockResolvedValue(
      detailRow({
        files: [
          // The primary slot for `character` is `sheet`; `worn` belongs to an
          // outfit and a mention binds none.
          file({ resource_id: '11', slot: 'sheet', sort_order: 1 }),
          file({ resource_id: '10', slot: 'sheet', sort_order: 0 }),
          file({ resource_id: '12', slot: 'worn', loadout_id: 'lo-1' }),
          file({ resource_id: '13', slot: 'stills' }),
        ],
      }),
    );
    fetchBundle.mockResolvedValue(
      bundle({ reference_resource_ids: ['10', '11'], prompt: { positive: 'Ava', negative: '' } }),
    );

    const out = await run([promptNode([AVA])]);

    expect(fetchDetail).toHaveBeenCalledWith(SCOPE, AVA_ID);
    expect(fetchBundle).toHaveBeenCalledWith(SCOPE, AVA_ID, {
      model: 'codex',
      loadoutId: undefined,
      selectedFileIds: ['10', '11'],
    });
    expect(out.reference_urls).toEqual([
      '/api/v1/resources/10/cover',
      '/api/v1/resources/11/cover',
    ]);
    expect(out.prompt_prefix).toBe('Ava');
  });

  it('a prompt asset has no primary FILE slot — it seeds an empty checklist', async () => {
    fetchDetail.mockResolvedValue(
      detailRow({ asset_type: 'prompt', files: [file({ slot: 'examples' })] }),
    );
    await run([promptNode([{ ...AVA, asset_type: 'prompt' }])]);
    expect(fetchBundle).toHaveBeenCalledWith(
      SCOPE,
      AVA_ID,
      expect.objectContaining({ selectedFileIds: [] }),
    );
  });

  it('reports what the bundle would not send, with the backend’s reason', async () => {
    fetchBundle.mockResolvedValue(
      bundle({ dropped: [{ resource_id: '11', reason: 'over_limit' }], max_refs: 1 }),
    );
    const out = await run([promptNode([AVA])]);
    const mention = out.contributions.find((c) => c.source === 'mention');
    expect(mention?.dropped).toEqual([{ resource_id: '11', reason: 'over_limit' }]);
    expect(mention?.nodeId).toBeNull();
  });

  it('a failed detail fetch fails only that mention, and is not swallowed', async () => {
    fetchDetail.mockRejectedValue(new Error('asset gone'));
    const out = await run([promptNode([AVA])]);
    const mention = out.contributions[0];
    expect(mention.error).toBe('asset gone');
    expect(mention.urls).toEqual([]);
    // The alternative — treating it as "this asset had nothing" — is the
    // silent drop: identical on screen to a genuinely empty asset.
    expect(out.reference_urls).toEqual([]);
  });

  it('names the precondition the user has to fix, before asking', async () => {
    const noModel = await resolveAssetInputs('p1', [promptNode([AVA])], [], {
      model: '',
      scopeId: SCOPE,
      fetch: fetchBundle as never,
      fetchDetail: fetchDetail as never,
    });
    expect(noModel.contributions[0].error).toBe('no_model');
    const noScope = await resolveAssetInputs('p1', [promptNode([AVA])], [], {
      model: 'codex',
      scopeId: '',
      fetch: fetchBundle as never,
      fetchDetail: fetchDetail as never,
    });
    expect(noScope.contributions[0].error).toBe('no_scope');
    expect(fetchDetail).not.toHaveBeenCalled();
  });

  it('the same asset named twice is bundled once', async () => {
    await run([promptNode([AVA, { ...AVA, name: 'Ava again' }])]);
    expect(fetchBundle).toHaveBeenCalledTimes(1);
  });

  it('a prompt with neither cards nor mentions asks nothing', async () => {
    const out = await run([node('p1', 'prompt', { body: 'x' })]);
    expect(fetchBundle).not.toHaveBeenCalled();
    expect(fetchDetail).not.toHaveBeenCalled();
    expect(out.contributions).toEqual([]);
  });
});

describe('ordering: wired cards lead, mentions follow', () => {
  it('puts a card’s references ahead of a mention’s', async () => {
    fetchDetail.mockResolvedValue(
      detailRow({ files: [file({ resource_id: '20', slot: 'sheet' })] }),
    );
    fetchBundle.mockImplementation(async (_scope: string, assetId: string) =>
      assetId === '900001'
        ? bundle({ reference_resource_ids: ['1'], prompt: { positive: 'wired', negative: '' } })
        : bundle({ reference_resource_ids: ['20'], prompt: { positive: 'mentioned', negative: '' } }),
    );

    const out = await run(
      [assetNode('a1', '900001'), promptNode([AVA])],
      [edge('a1', 'p1')],
    );

    // Both ceilings that trim references trim from the TAIL, so this order is
    // what decides which reference survives on a narrow provider.
    expect(out.reference_urls).toEqual([
      '/api/v1/resources/1/cover',
      '/api/v1/resources/20/cover',
    ]);
    expect(out.prompt_prefix).toBe('wired\nmentioned');
    expect(out.contributions.map((c) => c.source)).toEqual(['node', 'mention']);
  });
});

describe('provenance precedence', () => {
  it('a wired card wins over a mention', () => {
    expect(
      resolveAssetRef('p1', [assetNode('a1', '900001'), promptNode([AVA])], [edge('a1', 'p1')]),
    ).toEqual({ asset_id: '900001', loadout_id: null });
  });

  it('with no card, the FIRST mention answers — and binds no loadout', () => {
    expect(resolveAssetRef('p1', [promptNode([ALLEY, AVA])], [])).toEqual({
      asset_id: ALLEY_ID,
      loadout_id: null,
    });
  });

  it('no card and no mention is still null, not a guess', () => {
    expect(resolveAssetRef('p1', [node('p1', 'prompt', { body: 'x' })], [])).toBeNull();
  });
});
