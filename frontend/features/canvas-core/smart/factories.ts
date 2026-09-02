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

import { primarySlotFileIds, type AssetNodeSeed } from './assetFiles';
import type {
  AssetNodeData,
  SmartNode,
  LlmNodeData,
  MediaNode,
  MediaNodeData,
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
  CharacterNode,
  CharacterNodeData,
  LibEntityNodeData,
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
      // Hand-placed cards are unbound drafts (Task 3): binding a script_shots
      // row happens through the storyboard "open on canvas" flow / promote,
      // never through this composer factory.
      shot_id: data.shot_id ?? null,
      shot_label: data.shot_label ?? null,
      shot_type: data.shot_type ?? null,
      camera_angle: data.camera_angle ?? null,
      camera_movement: data.camera_movement ?? null,
      focal_length: data.focal_length ?? null,
      description: data.description ?? null,
      image_url: data.image_url ?? null,
      shot_status: data.shot_status ?? null,
      gen_task_id: data.gen_task_id ?? null,
      scene_id: data.scene_id ?? null,
    },
  };
}

export function createMediaNode(
  data: Partial<MediaNodeData> = {},
  opts: FactoryOptions = {},
): MediaNode {
  const random = opts.randomSuffix ?? DEFAULT_RANDOM_SUFFIX;
  return {
    id: makeId('media', random),
    type: 'media',
    position: opts.position ?? DEFAULT_POSITION,
    data: {
      title: data.title ?? 'Media',
      items: data.items ?? [],
    },
  };
}

export function createLlmNode(
  data: Partial<LlmNodeData> = {},
  opts: FactoryOptions = {},
): SmartNode<LlmNodeData> {
  const random = opts.randomSuffix ?? DEFAULT_RANDOM_SUFFIX;
  return {
    id: makeId('llm', random),
    type: 'llm',
    position: opts.position ?? DEFAULT_POSITION,
    data: {
      provider_slug: data.provider_slug ?? '',
      agent_id: data.agent_id ?? null,
      input_text: data.input_text ?? '',
      output_text: data.output_text ?? '',
      run_status: data.run_status ?? 'idle',
      run_error: data.run_error ?? null,
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
      resource_refs: data.resource_refs ?? [],
      // Negative prompt (Phase 2 asset library) — only persisted when
      // provided (absent = hand-typed prompt with no negative text).
      ...(data.negative_body ? { negative_body: data.negative_body } : {}),
      // Generation settings (G4-F1) — only persisted when provided (absent =
      // legacy text prompt; the character template seeds image branches).
      ...(data.gen ? { gen: data.gen } : {}),
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
      // G4-F2 optional fields — only present when provided (persisting
      // `undefined` keys into nodes_json would be noise).
      ...(data.images ? { images: data.images } : {}),
      ...(data.history_for ? { history_for: data.history_for } : {}),
    },
  };
}

export function createTimelineNode(
  data: Partial<import('./timeline').TimelineNodeData> = {},
  opts: FactoryOptions = {},
): SmartNode<import('./timeline').TimelineNodeData> {
  const random = opts.randomSuffix ?? DEFAULT_RANDOM_SUFFIX;
  return {
    id: makeId('timeline', random),
    type: 'timeline',
    position: opts.position ?? DEFAULT_POSITION,
    data: {
      segments: data.segments ?? [
        { id: `seg-${crypto.randomUUID()}`, prompt: '', seconds: 5 },
      ],
      model: data.model ?? '',
      aspect: data.aspect ?? '',
      run_status: data.run_status ?? 'idle',
      run_error: null,
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
      rounds: data.rounds ?? 1,
      round_start: data.round_start ?? 1,
      prompts: data.prompts ?? [''],
      show_prompt: data.show_prompt ?? true,
      image_input: data.image_input ?? false,
      image_batch_size: data.image_batch_size ?? 1,
    },
  };
}

/** Character canvas: the bible-card source node the preset workflow hangs off. */
export function createCharacterNode(
  data: Partial<CharacterNodeData> = {},
  opts: FactoryOptions = {},
): CharacterNode {
  const random = opts.randomSuffix ?? DEFAULT_RANDOM_SUFFIX;
  return {
    id: makeId('character', random),
    type: 'character',
    position: opts.position ?? DEFAULT_POSITION,
    data: {
      character_id: data.character_id ?? null,
      name: data.name ?? 'New character',
      role_tag: data.role_tag ?? '',
      description: data.description ?? '',
      portrait_url: data.portrait_url ?? null,
    },
  };
}

/** Location/prop canvas: the library-card source node (SP2). */
export function createLibEntityNode(
  type: 'location' | 'prop',
  data: Partial<LibEntityNodeData> = {},
  opts: FactoryOptions = {},
): SmartNode<LibEntityNodeData> {
  const random = opts.randomSuffix ?? DEFAULT_RANDOM_SUFFIX;
  return {
    id: makeId(type, random),
    type,
    position: opts.position ?? DEFAULT_POSITION,
    data: {
      entity_id: data.entity_id ?? null,
      name: data.name ?? (type === 'location' ? 'New location' : 'New prop'),
      badge_tag: data.badge_tag ?? '',
      description: data.description ?? '',
      cover_url: data.cover_url ?? null,
    },
  };
}

// `AssetNodeSeed` and the file predicates live in `./assetFiles` — one
// module owns "which files does an asset card reference", so seeding and
// rendering cannot answer it differently again. Re-exported here because a
// caller of `createAssetNode` naturally looks for its parameter type
// alongside it; this is the same declaration, not a second one.
export type { AssetNodeSeed };

export interface AssetFactoryOptions extends FactoryOptions {
  /** `asset_loadouts.id` to bind, or null/undefined for none. */
  loadoutId?: string | null;
}

/**
 * Asset-library reference card (P4 Task 4).
 *
 * Everything but `asset_id` / `loadout_id` / `selected_file_ids` is a display
 * SNAPSHOT — see `AssetNodeData`. `removed` is deliberately NOT seeded: its
 * absence means "not known to be gone", and writing `false` here would put a
 * claim into `nodes_json` that nothing has checked yet.
 */
export function createAssetNode(
  asset: AssetNodeSeed,
  opts: AssetFactoryOptions = {},
): SmartNode<AssetNodeData> {
  const random = opts.randomSuffix ?? DEFAULT_RANDOM_SUFFIX;
  const loadoutId = opts.loadoutId ?? null;
  return {
    id: makeId('asset', random),
    type: 'asset',
    position: opts.position ?? DEFAULT_POSITION,
    data: {
      asset_id: asset.id,
      loadout_id: loadoutId,
      selected_file_ids: primarySlotFileIds(asset, loadoutId),
      name: asset.name,
      asset_type: asset.asset_type,
      cover_file_id: asset.cover_file_id ?? null,
      readiness_state: asset.readiness?.state === 'ready' ? 'ready' : 'draft',
    },
  };
}
