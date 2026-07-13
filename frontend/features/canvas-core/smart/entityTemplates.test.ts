// features/canvas-core/smart/entityTemplates.test.ts
// SP2: location/prop preset workflows — card + 4 wired branches, prompt
// bodies inline the entity data (stateless-job model).

import { describe, expect, it } from 'vitest';

import { buildEntityTemplate } from './entityTemplates';

const asObj = (n: unknown) => n as Record<string, any>;

describe.each(['location', 'prop'] as const)('buildEntityTemplate(%s)', (type) => {
  it('seeds one card + 4 prompt/output pairs, fully wired', () => {
    const { nodes, connections } = buildEntityTemplate(type, { name: 'Booth' });
    const cards = nodes.filter((n) => asObj(n).type === type);
    const prompts = nodes.filter((n) => asObj(n).type === 'prompt');
    expect(cards).toHaveLength(1);
    expect(prompts).toHaveLength(4);
    expect(nodes.filter((n) => asObj(n).type === 'output')).toHaveLength(4);
    const cardId = asObj(cards[0]).id;
    expect(connections.filter((c) => c.source === cardId)).toHaveLength(4);
  });

  it('inlines name + description into every prompt body', () => {
    const { nodes } = buildEntityTemplate(type, {
      name: 'Booth',
      description: 'Cramped, smoke-stained.',
    });
    for (const n of nodes.filter((x) => asObj(x).type === 'prompt')) {
      expect(asObj(n).data.body).toContain('Booth');
      expect(asObj(n).data.body).toContain('Cramped, smoke-stained.');
    }
  });

  it('one text branch, three image branches; binds entity_id', () => {
    const { nodes } = buildEntityTemplate(type, { entity_id: '9', name: 'X' });
    const gens = nodes
      .filter((n) => asObj(n).type === 'prompt')
      .map((n) => asObj(n).data.gen);
    expect(gens.filter((g) => !g)).toHaveLength(1);
    expect(gens.filter(Boolean)).toHaveLength(3);
    const card = nodes.find((n) => asObj(n).type === type);
    expect(asObj(card).data.entity_id).toBe('9');
  });
});
