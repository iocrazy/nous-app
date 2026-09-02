// Generation provenance — `resolveAssetRef` walks upstream from a prompt to the
// asset card that owns the branch, so a dispatch can stamp `source_asset_id`
// (and the loadout) into generated_media.params.
//
// The ids are real wire shapes: `assets` router ids are JSON STRINGS, and the
// node data holds them as strings, so the fixtures use strings.

import { describe, expect, it } from 'vitest';

import { resolveAssetRef } from './assetRef';

const node = (id: string, type: string, data: Record<string, unknown> = {}) => ({
  id,
  type,
  position: { x: 0, y: 0 },
  data,
});

const asset = (id: string, data: Record<string, unknown>) =>
  node(id, 'asset', { loadout_id: null, selected_file_ids: [], ...data });

const edge = (source: string, target: string) => ({ id: `${source}->${target}`, source, target });

describe('resolveAssetRef', () => {
  it('finds a directly connected asset card and carries its loadout', () => {
    const nodes = [
      asset('a1', { asset_id: '727145299382534101', loadout_id: '727145299382534999' }),
      node('p1', 'prompt', {}),
    ];
    expect(resolveAssetRef('p1', nodes, [edge('a1', 'p1')])).toEqual({
      asset_id: '727145299382534101',
      loadout_id: '727145299382534999',
    });
  });

  it('reports a null loadout as null rather than omitting it', () => {
    const nodes = [asset('a1', { asset_id: '42' }), node('p1', 'prompt', {})];
    expect(resolveAssetRef('p1', nodes, [edge('a1', 'p1')])).toEqual({
      asset_id: '42',
      loadout_id: null,
    });
  });

  it('walks transitively through chained prompts', () => {
    const nodes = [
      asset('a1', { asset_id: '9' }),
      node('p1', 'prompt', {}),
      node('p2', 'prompt', {}),
    ];
    const edges = [edge('a1', 'p1'), edge('p1', 'p2')];
    expect(resolveAssetRef('p2', nodes, edges)).toEqual({ asset_id: '9', loadout_id: null });
  });

  it('prefers the nearest upstream asset (BFS order)', () => {
    const nodes = [
      asset('far', { asset_id: '1' }),
      asset('near', { asset_id: '2' }),
      node('mid', 'prompt', {}),
      node('p1', 'prompt', {}),
    ];
    const edges = [edge('far', 'mid'), edge('mid', 'p1'), edge('near', 'p1')];
    expect(resolveAssetRef('p1', nodes, edges)).toEqual({ asset_id: '2', loadout_id: null });
  });

  it('ignores an unbound card and returns null when nothing matches', () => {
    const nodes = [
      asset('a1', { asset_id: '' }),
      node('s1', 'shot', {}),
      node('p1', 'prompt', {}),
    ];
    const edges = [edge('a1', 'p1'), edge('s1', 'p1')];
    expect(resolveAssetRef('p1', nodes, edges)).toBeNull();
  });

  it('ignores a tombstoned card — a deleted asset is not provenance', () => {
    const nodes = [
      asset('a1', { asset_id: '5', removed: true }),
      node('p1', 'prompt', {}),
    ];
    expect(resolveAssetRef('p1', nodes, [edge('a1', 'p1')])).toBeNull();
  });

  it('ignores the legacy character/location/prop cards', () => {
    // Their ids are `_legacy_project_characters` / `_legacy_project_lib_entities`
    // rows, NOT `assets.id`. Answering with one would stamp the wrong table's
    // key into `source_asset_id`; migrating the cards is Task 6.
    const nodes = [
      node('c1', 'character', { character_id: '123456789', name: 'Ava' }),
      node('e1', 'location', { entity_id: '42', name: 'Docks' }),
      node('p1', 'prompt', {}),
    ];
    const edges = [edge('c1', 'p1'), edge('e1', 'p1')];
    expect(resolveAssetRef('p1', nodes, edges)).toBeNull();
  });

  it('survives a connection cycle without hanging', () => {
    const nodes = [node('p1', 'prompt', {}), node('p2', 'prompt', {})];
    const edges = [edge('p1', 'p2'), edge('p2', 'p1')];
    expect(resolveAssetRef('p2', nodes, edges)).toBeNull();
  });
});
