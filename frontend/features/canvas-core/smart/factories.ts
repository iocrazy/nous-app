/**
 * Smart-mode node factories (Phase 2 of canvas + AI upgrade).
 *
 * Creating a node off the main thread (composer click, paste, AI
 * insertion) should always go through one of these. They produce a
 * well-formed `SmartNode<...>` so the React Flow renderers can rely on
 * a stable `data` shape and don't need null-checks everywhere.
 *
 * IDs are generated with a short-lived counter PLUS a tiny random
 * suffix — collisions across reload are avoided by `preparePastedNodes`
 * (it dedupes on insert), so simple suffixing is fine here.
 */

import type {
  OutputKind,
  OutputNode,
  PromptNode,
  ShotNode,
  ShotNodeData,
  PromptNodeData,
  OutputNodeData,
  SmartNodeType,
  LoopNode,
  LoopNodeData,
} from './types';

interface Position {
  x: number;
  y: number;
}

let counter = 0;

/** Reset the per-session counter — used in tests so ids stay stable. */
export function _resetIdCounter(): void {
  counter = 0;
}

function makeId(prefix: SmartNodeType, randomSuffix: () => string): string {
  counter += 1;
  return `${prefix}-${counter}-${randomSuffix()}`;
}

export interface FactoryOptions {
  position?: Position;
  /** Override the random suffix generator (used by tests to make ids
   *  deterministic without monkey-patching Math.random). */
  randomSuffix?: () => string;
}

const DEFAULT_RANDOM_SUFFIX = (): string =>
  // 4-char alphanumeric — enough for runtime collision avoidance.
  Math.random().toString(36).slice(2, 6);

const DEFAULT_POSITION: Position = { x: 0, y: 0 };

export function createShotNode(
  data: Partial<ShotNodeData> = {},
  opts: FactoryOptions = {},
): ShotNode {
  const random = opts.randomSuffix ?? DEFAULT_RANDOM_SUFFIX;
  return {
    id: makeId('shot', random),
    type: 'shot',
    position: opts.position ?? DEFAULT_POSITION,
    data: {
      title: data.title ?? 'New shot',
      reference_resource_ids: data.reference_resource_ids ?? [],
      notes: data.notes ?? '',
    },
  };
}

export function createPromptNode(
  data: Partial<PromptNodeData> = {},
  opts: FactoryOptions = {},
): PromptNode {
  const random = opts.randomSuffix ?? DEFAULT_RANDOM_SUFFIX;
  return {
    id: makeId('prompt', random),
    type: 'prompt',
    position: opts.position ?? DEFAULT_POSITION,
    data: {
      body: data.body ?? '',
      provider_slug: data.provider_slug ?? '',
      agent_id: data.agent_id ?? null,
      run_status: data.run_status ?? 'idle',
      run_started_at: data.run_started_at ?? null,
      run_finished_at: data.run_finished_at ?? null,
      run_error: data.run_error ?? null,
    },
  };
}

export function createOutputNode(
  data: Partial<OutputNodeData> = {},
  opts: FactoryOptions = {},
): OutputNode {
  const random = opts.randomSuffix ?? DEFAULT_RANDOM_SUFFIX;
  const kind: OutputKind = data.kind ?? 'text';
  return {
    id: makeId('output', random),
    type: 'output',
    position: opts.position ?? DEFAULT_POSITION,
    data: {
      kind,
      resource_id: data.resource_id ?? null,
      preview_text: data.preview_text ?? '',
      preview_url: data.preview_url ?? null,
      crop_region: data.crop_region ?? null,
    },
  };
}

export function createLoopNode(
  data: Partial<LoopNodeData> = {},
  opts: FactoryOptions = {},
): LoopNode {
  const random = opts.randomSuffix ?? DEFAULT_RANDOM_SUFFIX;
  return {
    id: makeId('loop', random),
    type: 'loop',
    position: opts.position ?? DEFAULT_POSITION,
    data: {
      mode: data.mode ?? 'serial',
      label: data.label ?? '',
    },
  };
}
