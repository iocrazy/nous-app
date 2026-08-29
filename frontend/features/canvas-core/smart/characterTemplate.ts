// features/canvas-core/smart/characterTemplate.ts
//
// Preset workflow for the character canvas (kind='character', PR-CC2):
// opening an EMPTY character canvas seeds a bible-card node fanned into four
// agent branches — persona (text) / portrait / turnaround / expression sheet
// (image) — each Prompt pre-filled with a task template that inlines the
// character's name/description (stateless-job model: character data is
// injected at seeding, no per-character session).
//
// Pure function → unit-testable; the CanvasPage effect seeds it exactly once
// (only when nodes_json is empty) and persists through the normal save path.

import type { CanvasConnection, CanvasNode } from '../types';
import type { CharacterNodeData, PromptGenSettings } from './types';
import { createCharacterNode, createOutputNode, createPromptNode } from './factories';

export interface CharacterSeed {
  character_id?: string | null;
  name?: string;
  description?: string;
  role_tag?: string;
  portrait_url?: string | null;
}

interface Branch {
  key: 'persona' | 'portrait' | 'turnaround' | 'expression';
  body: (name: string, description: string) => string;
  gen: PromptGenSettings | null;
}

const describe = (name: string, description: string): string =>
  description ? `Character: ${name}\n${description}` : `Character: ${name}`;

const BRANCHES: Branch[] = [
  {
    key: 'persona',
    gen: null, // text — runs through the llm path
    body: (n, d) =>
      `Refine this character bio into a production-ready persona: backstory, ` +
      `temperament, speech pattern, core conflict, habits.\n\n${describe(n, d)}`,
  },
  {
    key: 'portrait',
    gen: { kind: 'image', model: '', ratio: '3:4', count: 1 },
    body: (n, d) =>
      `Cinematic head-and-shoulders portrait, neutral expression, keylight ` +
      `from the left.\n\n${describe(n, d)}`,
  },
  {
    key: 'turnaround',
    gen: { kind: 'image', model: '', ratio: '16:9', count: 1 },
    body: (n, d) =>
      `Character turnaround sheet: front, three-quarter, side and back views, ` +
      `consistent outfit and proportions, plain background.\n\n${describe(n, d)}`,
  },
  {
    key: 'expression',
    gen: { kind: 'image', model: '', ratio: 'auto', count: 1 },
    body: (n, d) =>
      `Expression sheet: joy, anger, sorrow, fear, surprise, deadpan — same ` +
      `face, grid layout.\n\n${describe(n, d)}`,
  },
];

const ROW_H = 190;
const CHARACTER_POS = { x: 40, y: 40 + ((BRANCHES.length - 1) * ROW_H) / 2 };

/** Build the preset node/connection set for one character. */
export function buildCharacterTemplate(seed: CharacterSeed = {}): {
  nodes: CanvasNode[];
  connections: CanvasConnection[];
} {
  const name = seed.name?.trim() || 'New character';
  const description = seed.description ?? '';

  const characterData: Partial<CharacterNodeData> = {
    character_id: seed.character_id ?? null,
    name,
    description,
    role_tag: seed.role_tag ?? '',
    portrait_url: seed.portrait_url ?? null,
  };
  const character = createCharacterNode(characterData, { position: CHARACTER_POS });

  const nodes: CanvasNode[] = [character as unknown as CanvasNode];
  const connections: CanvasConnection[] = [];

  BRANCHES.forEach((branch, i) => {
    const y = 40 + i * ROW_H;
    const prompt = createPromptNode(
      { body: branch.body(name, description), gen: branch.gen },
      { position: { x: 400, y } },
    );
    const output = createOutputNode(
      branch.gen ? { kind: branch.gen.kind } : {},
      { position: { x: 760, y } },
    );
    nodes.push(prompt as unknown as CanvasNode, output as unknown as CanvasNode);
    connections.push(
      {
        id: `conn-${character.id}-${prompt.id}`,
        source: character.id,
        target: prompt.id,
      },
      {
        id: `conn-${prompt.id}-${output.id}`,
        source: prompt.id,
        target: output.id,
      },
    );
  });

  return { nodes, connections };
}
