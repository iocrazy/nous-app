// features/canvas-core/smart/assetInputs.test.ts
//
// What an `asset` card hands the prompt it feeds (P4 Task 5).
//
// The `fetchBundle` stub answers the REAL wire shape of
// `GET /api/v1/assets/{id}/bundle`: the assets router serialises every id as a
// JSON STRING, `dropped` carries `{resource_id, reason}` objects, and `max_refs`
// is the provider's ceiling echoed back. Writing ideal-looking fixtures here is
// the discipline this repo files under 「边界 mock 必须用真实 JSON 形状」.

import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { CanvasConnection, CanvasNode } from '../types';
import {
  assetReferenceUrl,
  resolveAssetInputs,
  upstreamAssetNodes,
} from './promptInputs';
import type { AssetBundle } from '../../../services/assetsService';

const node = (id: string, type: string, data: Record<string, unknown> = {}): CanvasNode =>
  ({ id, type, position: { x: 0, y: 0 }, data }) as unknown as CanvasNode;

const assetNode = (
  id: string,
  over: Record<string, unknown> = {},
): CanvasNode =>
  node(id, 'asset', {
    asset_id: '727145299382534101',
    loadout_id: null,
    selected_file_ids: [],
    name: 'Ava',
    asset_type: 'character',
    cover_file_id: null,
    readiness_state: 'ready',
    ...over,
  });

const edge = (source: string, target: string): CanvasConnection =>
  ({ id: `${source}->${target}`, source, target }) as unknown as CanvasConnection;

/** A real-shaped bundle response. */
const bundle = (over: Partial<AssetBundle> = {}): AssetBundle => ({
  prompt: { positive: '', negative: '' },
  reference_resource_ids: [],
  dropped: [],
  max_refs: 9,
  ...over,
});

const SCOPE = '727145299382534100';

describe('upstreamAssetNodes', () => {
  it('finds asset cards one hop upstream, in connection order', () => {
    const nodes = [
      assetNode('a2', { asset_id: '2' }),
      assetNode('a1', { asset_id: '1' }),
      node('p1', 'prompt'),
    ];
    const conns = [edge('a1', 'p1'), edge('a2', 'p1')];
    expect(upstreamAssetNodes('p1', nodes, conns).map((c) => c.data.asset_id)).toEqual([
      '1',
      '2',
    ]);
  });

  it('does not walk transitively — one hop only', () => {
    // Provenance (`resolveAssetRef`) walks the whole chain; DELIVERY does not.
    // A card two prompts upstream fed THAT prompt, and re-sending its
    // references here would attach pictures the user wired somewhere else.
    const nodes = [assetNode('a1'), node('p1', 'prompt'), node('p2', 'prompt')];
    const conns = [edge('a1', 'p1'), edge('p1', 'p2')];
    expect(upstreamAssetNodes('p2', nodes, conns)).toEqual([]);
  });

  it('skips a tombstoned card and an unbound one', () => {
    const nodes = [
      assetNode('gone', { asset_id: '1', removed: true }),
      assetNode('unbound', { asset_id: '' }),
      node('p1', 'prompt'),
    ];
    const conns = [edge('gone', 'p1'), edge('unbound', 'p1')];
    expect(upstreamAssetNodes('p1', nodes, conns)).toEqual([]);
  });
});

describe('resolveAssetInputs', () => {
  let fetchBundle: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchBundle = vi.fn();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  const run = (nodes: CanvasNode[], conns: CanvasConnection[], model = 'codex') =>
    resolveAssetInputs('p1', nodes, conns, {
      model,
      scopeId: SCOPE,
      fetch: fetchBundle as never,
    });

  it('asks for nothing when no asset card feeds the prompt', async () => {
    const out = await run([node('p1', 'prompt')], []);
    expect(fetchBundle).not.toHaveBeenCalled();
    expect(out).toEqual({
      reference_urls: [],
      prompt_prefix: '',
      negative: '',
      contributions: [],
    });
  });

  it('sends the model and the card loadout to the bundle endpoint', async () => {
    fetchBundle.mockResolvedValue(bundle());
    const nodes = [
      assetNode('a1', { asset_id: '55', loadout_id: '99' }),
      node('p1', 'prompt'),
    ];
    await run(nodes, [edge('a1', 'p1')], 'seedream-4');
    expect(fetchBundle).toHaveBeenCalledWith(SCOPE, '55', {
      model: 'seedream-4',
      loadoutId: '99',
      selectedFileIds: [],
    });
  });

  it('omits loadoutId when the card binds no outfit', async () => {
    fetchBundle.mockResolvedValue(bundle());
    const nodes = [assetNode('a1', { asset_id: '55' }), node('p1', 'prompt')];
    await run(nodes, [edge('a1', 'p1')]);
    expect(fetchBundle.mock.calls[0][2]).toEqual({
      model: 'codex',
      loadoutId: undefined,
      selectedFileIds: [],
    });
  });

  // ── the selection goes TO the endpoint; the answer is taken as given ─────
  //
  // This block replaces a test that pinned a CLIENT-SIDE intersection against
  // an UNTRIMMED bundle — which is why the branch's central defect survived a
  // green suite. The endpoint trims the provider ceiling within the selection
  // now, so the only thing left to pin here is that the selection is sent and
  // the answer is not second-guessed.

  it('sends the card checklist so the ceiling trims within it', async () => {
    fetchBundle.mockResolvedValue(bundle());
    const nodes = [
      assetNode('a1', { asset_id: '55', selected_file_ids: ['12', '10'] }),
      node('p1', 'prompt'),
    ];
    await run(nodes, [edge('a1', 'p1')]);
    expect(fetchBundle.mock.calls[0][2].selectedFileIds).toEqual(['12', '10']);
  });

  it('sends an EMPTY checklist rather than none when nothing is ticked', async () => {
    // `undefined` would mean "no checklist" to the endpoint, and it answers
    // that with every file the asset owns. A card with every box unticked must
    // contribute zero references, not all of them.
    fetchBundle.mockResolvedValue(bundle());
    const nodes = [
      assetNode('a1', { selected_file_ids: [] }),
      node('p1', 'prompt'),
    ];
    await run(nodes, [edge('a1', 'p1')]);
    expect(fetchBundle.mock.calls[0][2].selectedFileIds).toEqual([]);
    expect(fetchBundle.mock.calls[0][2].selectedFileIds).not.toBeUndefined();
  });

  it('delivers what the bundle answered, in BUNDLE order, without re-filtering', async () => {
    // The endpoint already applied the checklist. Intersecting again here is
    // how `top_N(selection)` silently became `top_N(all) ∩ selection`: a
    // low-priority pick that the endpoint DID deliver would be filtered back
    // out by a stale client-side copy of the selection.
    fetchBundle.mockResolvedValue(
      bundle({ reference_resource_ids: ['10', '12'], max_refs: 2 }),
    );
    const nodes = [
      assetNode('a1', { selected_file_ids: ['12', '10', '77'] }),
      node('p1', 'prompt'),
    ];
    const out = await run(nodes, [edge('a1', 'p1')]);
    expect(out.reference_urls).toEqual([
      assetReferenceUrl('10'),
      assetReferenceUrl('12'),
    ]);
  });

  it('keeps a delivered reference the local selection no longer lists', async () => {
    // The endpoint is the authority on what was sent. If the two ever
    // disagree, reporting the run as having shipped LESS than it did is the
    // dishonest direction — the reference reached the provider either way.
    fetchBundle.mockResolvedValue(bundle({ reference_resource_ids: ['44'] }));
    const nodes = [
      assetNode('a1', { selected_file_ids: ['10'] }),
      node('p1', 'prompt'),
    ];
    const out = await run(nodes, [edge('a1', 'p1')]);
    expect(out.reference_urls).toEqual([assetReferenceUrl('44')]);
  });

  it('reports nothing dropped when everything chosen was sent (I1)', async () => {
    // The badge cried wolf on the happy path before this: the endpoint put
    // every non-top-N file of the ASSET into `dropped`, so a freshly placed
    // card — which seeds the primary slot alone — reported drops on every run.
    // A badge that fires on the default path stops being read, which then
    // costs the real case its only warning.
    fetchBundle.mockResolvedValue(
      bundle({ reference_resource_ids: ['10'], dropped: [], max_refs: 3 }),
    );
    const nodes = [
      assetNode('a1', { selected_file_ids: ['10'] }),
      node('p1', 'prompt'),
    ];
    const out = await run(nodes, [edge('a1', 'p1')]);
    expect(out.contributions[0].dropped).toEqual([]);
    expect(out.reference_urls).toEqual([assetReferenceUrl('10')]);
  });

  it('carries a drop whose id IS in the selection, verbatim', async () => {
    // The overlap case that no test on this branch had: `dropped` and the
    // checklist share an id, because the pick genuinely lost the priority
    // contest inside its own selection. That is what makes the badge a
    // per-run answer instead of a standing description of the asset.
    fetchBundle.mockResolvedValue(
      bundle({
        reference_resource_ids: ['10'],
        dropped: [{ resource_id: '12', reason: 'over_limit' }],
        max_refs: 1,
      }),
    );
    const nodes = [
      assetNode('a1', { selected_file_ids: ['10', '12'] }),
      node('p1', 'prompt'),
    ];
    const out = await run(nodes, [edge('a1', 'p1')]);
    expect(out.contributions[0].dropped).toEqual([
      { resource_id: '12', reason: 'over_limit' },
    ]);
  });

  it('emits the relative resource-cover shape the backend bridge accepts', async () => {
    fetchBundle.mockResolvedValue(bundle({ reference_resource_ids: ['10'] }));
    const nodes = [assetNode('a1', { selected_file_ids: ['10'] }), node('p1', 'prompt')];
    const out = await run(nodes, [edge('a1', 'p1')]);
    expect(out.reference_urls).toEqual(['/api/v1/resources/10/cover']);
  });

  it('composes two cards: upstream order, deduped urls, newline-joined prompts', async () => {
    fetchBundle.mockImplementation(async (_scope: string, assetId: string) =>
      assetId === '1'
        ? bundle({
            prompt: { positive: 'Ava, red coat', negative: 'blurry' },
            reference_resource_ids: ['10', '20'],
          })
        : bundle({
            prompt: { positive: 'the Docks at night', negative: 'blurry' },
            reference_resource_ids: ['20', '30'],
          }),
    );
    const nodes = [
      assetNode('a1', { asset_id: '1', selected_file_ids: ['10', '20'] }),
      assetNode('a2', { asset_id: '2', selected_file_ids: ['20', '30'] }),
      node('p1', 'prompt'),
    ];
    const out = await run(nodes, [edge('a1', 'p1'), edge('a2', 'p1')]);
    expect(out.reference_urls).toEqual([
      assetReferenceUrl('10'),
      assetReferenceUrl('20'),
      assetReferenceUrl('30'),
    ]);
    expect(out.prompt_prefix).toBe('Ava, red coat\nthe Docks at night');
    // One negative, not two identical ones.
    expect(out.negative).toBe('blurry');
  });

  it('reports what the bundle would not send, per card', async () => {
    fetchBundle.mockResolvedValue(
      bundle({
        reference_resource_ids: ['10'],
        dropped: [
          { resource_id: '11', reason: 'over_limit' },
          { resource_id: '12', reason: 'no_image_file' },
        ],
        max_refs: 1,
      }),
    );
    const nodes = [assetNode('a1', { selected_file_ids: ['10'] }), node('p1', 'prompt')];
    const out = await run(nodes, [edge('a1', 'p1')]);
    expect(out.contributions).toHaveLength(1);
    expect(out.contributions[0].nodeId).toBe('a1');
    expect(out.contributions[0].dropped).toEqual([
      { resource_id: '11', reason: 'over_limit' },
      { resource_id: '12', reason: 'no_image_file' },
    ]);
    expect(out.contributions[0].error).toBeNull();
  });

  it('reports a clean bundle as an empty drop list, not as nothing', async () => {
    fetchBundle.mockResolvedValue(bundle({ reference_resource_ids: ['10'] }));
    const nodes = [assetNode('a1', { selected_file_ids: ['10'] }), node('p1', 'prompt')];
    const out = await run(nodes, [edge('a1', 'p1')]);
    expect(out.contributions[0]).toMatchObject({ dropped: [], error: null });
  });

  it('fails ONE card without throwing away the card beside it', async () => {
    fetchBundle.mockImplementation(async (_scope: string, assetId: string) => {
      if (assetId === '1') throw new Error('HTTP 500');
      return bundle({
        prompt: { positive: 'the Docks', negative: '' },
        reference_resource_ids: ['30'],
      });
    });
    const nodes = [
      assetNode('a1', { asset_id: '1', selected_file_ids: ['10'] }),
      assetNode('a2', { asset_id: '2', selected_file_ids: ['30'] }),
      node('p1', 'prompt'),
    ];
    const out = await run(nodes, [edge('a1', 'p1'), edge('a2', 'p1')]);
    expect(out.reference_urls).toEqual([assetReferenceUrl('30')]);
    expect(out.prompt_prefix).toBe('the Docks');
    expect(out.contributions[0].error).toBe('HTTP 500');
    expect(out.contributions[1].error).toBeNull();
  });

  it('reports a missing scope instead of silently contributing nothing', async () => {
    // An empty `scope_id` is a 403 `not_a_member`, not an unscoped query. A run
    // that just skipped the card would look exactly like a card with no files.
    const nodes = [assetNode('a1', { selected_file_ids: ['10'] }), node('p1', 'prompt')];
    const out = await resolveAssetInputs('p1', nodes, [edge('a1', 'p1')], {
      model: 'codex',
      scopeId: '',
      fetch: fetchBundle as never,
    });
    expect(fetchBundle).not.toHaveBeenCalled();
    expect(out.contributions[0].error).toBe('no_scope');
    expect(out.reference_urls).toEqual([]);
  });
});
