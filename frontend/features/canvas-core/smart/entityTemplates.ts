// features/canvas-core/smart/entityTemplates.ts
//
// Preset workflows for the location and prop canvases (SP2) — the
// generalized siblings of characterTemplate. Same stateless-job model: the
// entity's name/description are inlined into each branch prompt at seeding.

import type { CanvasConnection, CanvasNode } from '../types';
import type { PromptGenSettings } from './types';
import { createLibEntityNode, createOutputNode, createPromptNode } from './factories';

export interface EntitySeed {
  entity_id?: string | null;
  name?: string;
  description?: string;
  badge_tag?: string;
  cover_url?: string | null;
}

interface Branch {
  body: (name: string, description: string) => string;
  gen: PromptGenSettings | null;
}

const describe = (label: string, name: string, description: string): string =>
  description ? `${label}: ${name}\n${description}` : `${label}: ${name}`;

const LOCATION_BRANCHES: Branch[] = [
  {
    gen: null,
    body: (n, d) =>
      `Develop this location into a production design brief: era, mood, ` +
      `palette, key set pieces, sound of the space, how scenes stage in it.` +
      `\n\n${describe('Location', n, d)}`,
  },
  {
    gen: { kind: 'image', model: '', ratio: '16:9', count: 1 },
    body: (n, d) =>
      `Cinematic establishing concept art, wide shot, rich atmosphere.` +
      `\n\n${describe('Location', n, d)}`,
  },
  {
    gen: { kind: 'image', model: '', ratio: 'auto', count: 1 },
    body: (n, d) =>
      `Top-down floor plan / staging diagram, clean linework, labeled zones.` +
      `\n\n${describe('Location', n, d)}`,
  },
  {
    gen: { kind: 'image', model: '', ratio: '16:9', count: 1 },
    body: (n, d) =>
      `Day-and-night mood pair, same camera, lighting contrast study.` +
      `\n\n${describe('Location', n, d)}`,
  },
];

const PROP_BRANCHES: Branch[] = [
  {
    gen: null,
    body: (n, d) =>
      `Develop this prop into a design brief: function in story, period ` +
      `accuracy, materials, wear-and-tear history, who handles it and how.` +
      `\n\n${describe('Prop', n, d)}`,
  },
  {
    gen: { kind: 'image', model: '', ratio: 'auto', count: 1 },
    body: (n, d) =>
      `Hero product-style render on plain background, studio lighting.` +
      `\n\n${describe('Prop', n, d)}`,
  },
  {
    gen: { kind: 'image', model: '', ratio: '16:9', count: 1 },
    body: (n, d) =>
      `Prop turnaround sheet: front, side, back and detail views, consistent ` +
      `scale, plain background, model sheet style.\n\n${describe('Prop', n, d)}`,
  },
  {
    gen: { kind: 'image', model: '', ratio: 'auto', count: 1 },
    body: (n, d) =>
      `Extreme close-up material study: texture, wear, engravings, patina.` +
      `\n\n${describe('Prop', n, d)}`,
  },
];

const ROW_H = 190;

/** Build the preset node/connection set for one location or prop. */
export function buildEntityTemplate(
  type: 'location' | 'prop',
  seed: EntitySeed = {},
): { nodes: CanvasNode[]; connections: CanvasConnection[] } {
  const branches = type === 'location' ? LOCATION_BRANCHES : PROP_BRANCHES;
  const name =
    seed.name?.trim() || (type === 'location' ? 'New location' : 'New prop');
  const description = seed.description ?? '';

  const card = createLibEntityNode(
    type,
    {
      entity_id: seed.entity_id ?? null,
      name,
      description,
      badge_tag: seed.badge_tag ?? '',
      cover_url: seed.cover_url ?? null,
    },
    { position: { x: 40, y: 40 + ((branches.length - 1) * ROW_H) / 2 } },
  );

  const nodes: CanvasNode[] = [card as unknown as CanvasNode];
  const connections: CanvasConnection[] = [];

  branches.forEach((branch, i) => {
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
      { id: `conn-${card.id}-${prompt.id}`, source: card.id, target: prompt.id },
      { id: `conn-${prompt.id}-${output.id}`, source: prompt.id, target: output.id },
    );
  });

  return { nodes, connections };
}
