// CC5 asset backlink — resolveEntityRef walks upstream from a prompt to the
// entity card (character/location/prop) that owns the branch, so generation
// dispatches can stamp entity ownership into generated_media.params.

import { describe, expect, it } from 'vitest';

import { resolveEntityRef } from './entityRef';

const node = (id: string, type: string, data: Record<string, unknown> = {}) => ({
  id,
  type,
  position: { x: 0, y: 0 },
  data,
});

const edge = (source: string, target: string) => ({ id: `${source}->${target}`, source, target });

describe('resolveEntityRef', () => {
  it('finds a directly connected character card', () => {
    const nodes = [
      node('c1', 'character', { character_id: '123456789', name: 'Ava' }),
      node('p1', 'prompt', {}),
    ];
    expect(resolveEntityRef('p1', nodes, [edge('c1', 'p1')])).toEqual({
      kind: 'character',
      id: '123456789',
    });
  });

  it('finds a location/prop card and reports its node type as kind', () => {
    const nodes = [
      node('e1', 'location', { entity_id: '42', name: 'Docks' }),
      node('e2', 'prop', { entity_id: '77', name: 'Dagger' }),
      node('p1', 'prompt', {}),
      node('p2', 'prompt', {}),
    ];
    expect(resolveEntityRef('p1', nodes, [edge('e1', 'p1')])).toEqual({
      kind: 'location',
      id: '42',
    });
    expect(resolveEntityRef('p2', nodes, [edge('e2', 'p2')])).toEqual({
      kind: 'prop',
      id: '77',
    });
  });

  it('walks transitively through chained prompts', () => {
    const nodes = [
      node('c1', 'character', { character_id: '9' }),
      node('p1', 'prompt', {}),
      node('p2', 'prompt', {}),
    ];
    const edges = [edge('c1', 'p1'), edge('p1', 'p2')];
    expect(resolveEntityRef('p2', nodes, edges)).toEqual({ kind: 'character', id: '9' });
  });

  it('ignores unbound cards (null id) and returns null when nothing matches', () => {
    const nodes = [
      node('c1', 'character', { character_id: null }),
      node('s1', 'shot', {}),
      node('p1', 'prompt', {}),
    ];
    const edges = [edge('c1', 'p1'), edge('s1', 'p1')];
    expect(resolveEntityRef('p1', nodes, edges)).toBeNull();
  });

  it('survives a connection cycle without hanging', () => {
    const nodes = [node('p1', 'prompt', {}), node('p2', 'prompt', {})];
    const edges = [edge('p1', 'p2'), edge('p2', 'p1')];
    expect(resolveEntityRef('p2', nodes, edges)).toBeNull();
  });

  it('prefers the nearest upstream entity (BFS order)', () => {
    const nodes = [
      node('far', 'character', { character_id: '1' }),
      node('near', 'character', { character_id: '2' }),
      node('mid', 'prompt', {}),
      node('p1', 'prompt', {}),
    ];
    const edges = [edge('far', 'mid'), edge('mid', 'p1'), edge('near', 'p1')];
    expect(resolveEntityRef('p1', nodes, edges)).toEqual({ kind: 'character', id: '2' });
  });
});
