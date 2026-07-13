// features/canvas-core/smart/characterTemplate.test.ts
// Preset workflow template (PR-CC2): bible card + 4 agent branches, prompt
// bodies inline the character data (stateless-job model, no session).

import { describe, expect, it } from 'vitest';

import { buildCharacterTemplate } from './characterTemplate';

const asObj = (n: unknown) => n as Record<string, any>;

describe('buildCharacterTemplate', () => {
  it('seeds one character card + 4 prompt/output branch pairs, fully wired', () => {
    const { nodes, connections } = buildCharacterTemplate({ name: 'Cole' });

    const characters = nodes.filter((n) => asObj(n).type === 'character');
    const prompts = nodes.filter((n) => asObj(n).type === 'prompt');
    const outputs = nodes.filter((n) => asObj(n).type === 'output');
    expect(characters).toHaveLength(1);
    expect(prompts).toHaveLength(4);
    expect(outputs).toHaveLength(4);

    // Wiring: character → each prompt, each prompt → its output.
    const charId = asObj(characters[0]).id;
    const fromChar = connections.filter((c) => c.source === charId);
    expect(fromChar).toHaveLength(4);
    for (const p of prompts) {
      expect(connections.some((c) => c.source === asObj(p).id)).toBe(true);
    }
  });

  it('inlines name + description into every prompt body', () => {
    const { nodes } = buildCharacterTemplate({
      name: 'Cole',
      description: 'A retired cavalry officer.',
    });
    const bodies = nodes
      .filter((n) => asObj(n).type === 'prompt')
      .map((n) => asObj(n).data.body as string);
    for (const body of bodies) {
      expect(body).toContain('Character: Cole');
      expect(body).toContain('A retired cavalry officer.');
    }
  });

  it('one text branch (persona), three image branches with distinct ratios', () => {
    const { nodes } = buildCharacterTemplate({ name: 'Cole' });
    const gens = nodes
      .filter((n) => asObj(n).type === 'prompt')
      .map((n) => asObj(n).data.gen);
    // persona = text: no gen persisted (factory omits falsy gen; the view's
    // destructuring default folds absent → null).
    expect(gens.filter((g) => !g)).toHaveLength(1);
    const ratios = gens.filter(Boolean).map((g) => g.ratio);
    expect(new Set(ratios).size).toBe(3); // 3:4 / 16:9 / 1:1
  });

  it('binds the library row when a character_id is passed', () => {
    const { nodes } = buildCharacterTemplate({ character_id: '42', name: 'Cole' });
    const card = nodes.find((n) => asObj(n).type === 'character');
    expect(asObj(card).data.character_id).toBe('42');
  });

  it('unnamed seed falls back to a placeholder name', () => {
    const { nodes } = buildCharacterTemplate();
    const card = nodes.find((n) => asObj(n).type === 'character');
    expect(asObj(card).data.name).toBe('New character');
  });
});
