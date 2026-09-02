// features/canvas-core/smart/legacyMigration.test.ts
//
// Turning pre-P3 entity cards into asset cards (P4 Task 6).
//
// The two halves are tested apart because they are apart in the code: the
// async half asks the server, the sync half applies the answers to whatever
// the node list is at the moment of the write. Every id is a string, which is
// what `/api/v1/assets` and `resolve-legacy` both put on the wire.

import { describe, expect, it, vi } from 'vitest';

import type { AssetRow } from '../../../services/assetsService';
import type { CanvasNode } from '../types';
import { _resetIdCounter } from './factories';
import {
  applyLegacyVerdicts,
  legacyCards,
  markUnmigrated,
  resolveLegacyVerdicts,
  toAssetNode,
  type LegacyVerdict,
} from './legacyMigration';

const ASSET_ID = '727145299382534300';

function assetRow(over: Partial<AssetRow> = {}): AssetRow {
  return {
    id: ASSET_ID,
    scope_id: '727145299382534100',
    asset_type: 'character',
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
    cover_file_id: null,
    // `migrated` / not in the library, because that is what this fixture
    // MODELS: the asset a pre-P3 card resolves to was created by the
    // backfill, and mig 449 puts migration-created rows OUT of the library
    // (membership is a deliberate act). It is also why the canvas asset
    // picker has to ask for `library: 'all'` — the default `'in'` would hide
    // exactly these rows, leaving a user able to see a migrated card on the
    // board and unable to find its asset in the picker.
    source: 'migrated',
    in_library: false,
    duplicated_from: null,
    is_system_preset: false,
    tags: {},
    sort_order: 0,
    created_by: null,
    created_at: '2026-09-01T00:00:00+00:00',
    updated_at: '2026-09-01T00:00:00+00:00',
    readiness: { state: 'ready', missing: [] },
    file_counts_by_slot: {},
    project_ids: [],
    loadout_count: 0,
    ...over,
  };
}

const characterCard = (over: Record<string, unknown> = {}): CanvasNode => ({
  id: 'character-1-abcd',
  type: 'character',
  position: { x: 120, y: 60 },
  width: 280,
  data: {
    character_id: '400000000000000001',
    name: 'Cole',
    role_tag: 'lead',
    description: 'A dockworker',
    portrait_url: null,
  },
  ...over,
});

const entityCard = (type: 'location' | 'prop', entityId: string | null): CanvasNode => ({
  id: `${type}-1-abcd`,
  type,
  position: { x: 0, y: 0 },
  data: {
    entity_id: entityId,
    name: 'Dock',
    badge_tag: '',
    description: '',
    cover_url: null,
  },
});

describe('legacyCards', () => {
  it('reads character_id from a character and entity_id from the other two', () => {
    const cards = legacyCards([
      characterCard(),
      entityCard('location', '400000000000000002'),
      entityCard('prop', '400000000000000003'),
    ]);
    expect(cards).toEqual([
      { nodeId: 'character-1-abcd', kind: 'character', legacyId: '400000000000000001' },
      { nodeId: 'location-1-abcd', kind: 'location', legacyId: '400000000000000002' },
      { nodeId: 'prop-1-abcd', kind: 'prop', legacyId: '400000000000000003' },
    ]);
  });

  it('ignores an unbound card and every non-legacy node type', () => {
    expect(
      legacyCards([
        characterCard({ data: { character_id: null, name: 'Hand-placed' } }),
        entityCard('location', null),
        { id: 'p1', type: 'prompt', position: { x: 0, y: 0 }, data: { body: '' } },
        { id: 'a1', type: 'asset', position: { x: 0, y: 0 }, data: { asset_id: '9' } },
      ]),
    ).toEqual([]);
  });

  it('accepts a numeric legacy id from a hand-written document', () => {
    const card = characterCard({ data: { character_id: 42, name: 'Cole' } });
    expect(legacyCards([card])[0].legacyId).toBe('42');
  });
});

describe('toAssetNode', () => {
  it('keeps the node id, the position and the size — only type and data change', () => {
    const before = characterCard();
    const after = toAssetNode(before, assetRow()) as Record<string, unknown>;
    expect(after.id).toBe('character-1-abcd');
    expect(after.position).toEqual({ x: 120, y: 60 });
    expect(after.width).toBe(280);
    expect(after.type).toBe('asset');
    expect(after.data).toMatchObject({
      asset_id: ASSET_ID,
      loadout_id: null,
      name: 'Cole Bannon',
      asset_type: 'character',
      readiness_state: 'ready',
    });
  });

  it('leaves every edge on the canvas valid, because the id did not move', () => {
    const before = characterCard();
    const edges = [
      { id: 'e1', source: 'character-1-abcd', target: 'prompt-2' },
      { id: 'e2', source: 'prompt-2', target: 'character-1-abcd' },
    ];
    const after = toAssetNode(before, assetRow()) as { id: string };
    const ids = new Set([after.id, 'prompt-2']);
    expect(edges.every((e) => ids.has(e.source) && ids.has(e.target))).toBe(true);
  });
});

describe('resolveLegacyVerdicts', () => {
  it('asks once per bound card, with that card\'s own kind and id', async () => {
    const resolve = vi.fn().mockResolvedValue(null);
    const fetchDetail = vi.fn();
    await resolveLegacyVerdicts(
      [characterCard(), entityCard('prop', '400000000000000003')],
      { resolve, fetchDetail },
    );
    expect(resolve.mock.calls).toEqual([
      ['character', '400000000000000001'],
      ['prop', '400000000000000003'],
    ]);
    expect(fetchDetail).not.toHaveBeenCalled();
  });

  it('asks nothing at all when the canvas holds no legacy card', async () => {
    const resolve = vi.fn();
    const verdicts = await resolveLegacyVerdicts(
      [{ id: 'a1', type: 'asset', position: { x: 0, y: 0 }, data: { asset_id: '9' } }],
      { resolve, fetchDetail: vi.fn() },
    );
    expect(verdicts).toEqual([]);
    expect(resolve).not.toHaveBeenCalled();
  });

  it('holds the concurrency cap', async () => {
    let inFlight = 0;
    let peak = 0;
    const resolve = vi.fn(async () => {
      inFlight += 1;
      peak = Math.max(peak, inFlight);
      await Promise.resolve();
      inFlight -= 1;
      return null;
    });
    const nodes = Array.from({ length: 9 }, (_, i) => ({
      id: `character-${i}`,
      type: 'character',
      position: { x: 0, y: 0 },
      data: { character_id: String(i + 1) },
    })) as CanvasNode[];
    await resolveLegacyVerdicts(nodes, { resolve, fetchDetail: vi.fn(), concurrency: 3 });
    expect(resolve).toHaveBeenCalledTimes(9);
    expect(peak).toBeLessThanOrEqual(3);
  });

  it('a failed resolve is `unresolved`, never a verdict about the asset', async () => {
    const verdicts = await resolveLegacyVerdicts([characterCard()], {
      resolve: vi.fn().mockRejectedValue(new Error('offline')),
      fetchDetail: vi.fn(),
    });
    expect(verdicts).toEqual([{ nodeId: 'character-1-abcd', kind: 'unresolved' }]);
  });

  it('a hit whose DETAIL fetch fails is also `unresolved`', async () => {
    const verdicts = await resolveLegacyVerdicts([characterCard()], {
      resolve: vi.fn().mockResolvedValue(ASSET_ID),
      fetchDetail: vi.fn().mockRejectedValue(new Error('500')),
    });
    expect(verdicts).toEqual([{ nodeId: 'character-1-abcd', kind: 'unresolved' }]);
  });
});

describe('applyLegacyVerdicts', () => {
  it('a hit becomes an asset card in place', () => {
    _resetIdCounter();
    const nodes = [characterCard()];
    const out = applyLegacyVerdicts(nodes, [
      { nodeId: 'character-1-abcd', kind: 'migrated', asset: assetRow() },
    ]);
    expect(out.migrated).toEqual(['character-1-abcd']);
    expect(out.unchanged).toBe(false);
    const node = out.nodes[0] as Record<string, unknown>;
    expect(node.id).toBe('character-1-abcd');
    expect(node.type).toBe('asset');
    expect(node.position).toEqual({ x: 120, y: 60 });
  });

  it('a miss keeps the legacy card and flags it — no deletion, no guess', () => {
    const nodes = [characterCard()];
    const out = applyLegacyVerdicts(nodes, [
      { nodeId: 'character-1-abcd', kind: 'unmigrated' },
    ]);
    expect(out.unmigrated).toEqual(['character-1-abcd']);
    const node = out.nodes[0] as { type: string; data: Record<string, unknown> };
    expect(node.type).toBe('character');
    expect(node.data.unmigrated).toBe(true);
    // The card's own content is untouched — the badge is additive.
    expect(node.data.character_id).toBe('400000000000000001');
    expect(node.data.name).toBe('Cole');
  });

  it('an unresolved card is returned byte for byte, with no flag', () => {
    const nodes = [characterCard()];
    const out = applyLegacyVerdicts(nodes, [
      { nodeId: 'character-1-abcd', kind: 'unresolved' },
    ]);
    expect(out.nodes[0]).toBe(nodes[0]);
    expect(out.unresolved).toEqual(['character-1-abcd']);
    expect(out.unchanged).toBe(true);
    expect((out.nodes[0] as { data: Record<string, unknown> }).data.unmigrated).toBeUndefined();
  });

  it('an already-flagged miss reports no change, so an idle load writes nothing', () => {
    const nodes = [markUnmigrated(characterCard())];
    const out = applyLegacyVerdicts(nodes, [
      { nodeId: 'character-1-abcd', kind: 'unmigrated' },
    ]);
    expect(out.unchanged).toBe(true);
    expect(out.nodes).toBe(nodes);
  });

  it('applies to the LIVE list: a node deleted mid-flight is not resurrected', () => {
    const verdicts: LegacyVerdict[] = [
      { nodeId: 'character-1-abcd', kind: 'migrated', asset: assetRow() },
    ];
    const live: CanvasNode[] = [
      { id: 'prompt-9', type: 'prompt', position: { x: 0, y: 0 }, data: {} },
    ];
    const out = applyLegacyVerdicts(live, verdicts);
    expect(out.nodes).toBe(live);
    expect(out.migrated).toEqual([]);
    expect(out.unchanged).toBe(true);
  });

  it('applies to the LIVE list: a drag during the round trip survives', () => {
    const dragged = characterCard({ position: { x: 999, y: 777 } });
    const out = applyLegacyVerdicts(
      [dragged],
      [{ nodeId: 'character-1-abcd', kind: 'migrated', asset: assetRow() }],
    );
    expect((out.nodes[0] as { position: unknown }).position).toEqual({ x: 999, y: 777 });
  });

  it('leaves every other node alone, by reference', () => {
    const other: CanvasNode = {
      id: 'prompt-9',
      type: 'prompt',
      position: { x: 0, y: 0 },
      data: {},
    };
    const out = applyLegacyVerdicts(
      [other, characterCard()],
      [{ nodeId: 'character-1-abcd', kind: 'unmigrated' }],
    );
    expect(out.nodes[0]).toBe(other);
  });
});
