/**
 * Smart-mode node-type definitions (Phase 2 of canvas + AI upgrade).
 *
 *   shot    — describes a frame: title + reference image refs + notes
 *   prompt  — the AI generation step that turns one or more shots into
 *             generated content. Owns the run config.
 *   output  — the rendered result of a prompt run (image / video / text).
 *
 * The smart-mode graph topology is shot(s) → prompt → output. The
 * connect rules are enforced by `canConnectSmart` in this module; the
 * UI layer just calls into it.
 */

import type { CropRegion } from '../editor/types';
import type { CanvasNode } from '../types';

/**
 * A resource @-referenced inside a PromptNode body.
 * Shape mirrors ResourceRefAttachment (frontend/types.ts) minus the 'kind'
 * discriminant — stored per-canvas-node so the runner can forward refs as
 * context attachments without re-fetching the resource record.
 */
export interface PromptResourceRef {
  resource_id: string;   // Snowflake as string (bigint-safe)
  name: string;          // display name snapshot
  kind: 'video' | 'image' | 'doc' | 'audio' | 'pdf';
  mime: string;
  scope: { type: 'personal' | 'team'; id: string };
}

export type SmartNodeType =
  | 'shot'
  | 'media'
  | 'llm'
  | 'prompt'
  | 'output'
  | 'loop'
  | 'timeline'
  | 'group'
  | 'character'
  | 'location'
  | 'prop';

export type LoopMode = 'serial' | 'parallel';

/**
 * shot node (storyboard canvas epic, Task 3): source-card fields (title/
 * reference_resource_ids/notes) are the pre-existing hand-placed-draft
 * shape (legacy compat — `shot_id: null` nodes render exactly as before).
 * The binding-mirror fields below light up when the canvas is opened from
 * the storyboard tier and the node is bound to a `script_shots` row: the
 * mirror is written on open (reconcile) and echoed on every field edit
 * (optimistic PATCH `script_shots` + revert-on-failure — this node never
 * owns the row, `script_shots` does).
 */
export interface ShotNodeData {
  title: string;
  /** Snowflake resource IDs (strings to avoid bigint precision loss). */
  reference_resource_ids: string[];
  notes: string;
  /** Bound `script_shots.id` (Snowflake string); null = unbound hand-placed
   *  draft node (legacy compat — pre-binding canvases). */
  shot_id: string | null;
  /** Binding-mirror fields (populate on canvas open reconcile; echoed on
   *  every editable-field PATCH): */
  shot_label: string | null; // shot number/code, e.g. "1-2"
  shot_type: string | null; // 景别
  camera_angle: string | null;
  camera_movement: string | null;
  focal_length: string | null;
  description: string | null;
  image_url: string | null; // rendered frame
  shot_status: string | null;
  /** In-flight generation task id (Task 3 fix-round-2 — prompt node's
   *  `gen_tasks` parity, singular since a shot dispatches one task at a
   *  time): patched immediately once `dispatchGenerations` returns, so a
   *  reload/nav-away-and-back can re-attach polling by task id instead of
   *  guessing the outcome from `image_url` alone. Cleared once the task
   *  settles (success or failure). Pure frontend mirror — the merged
   *  backend (Task 2) never reads or writes this field; it only reads
   *  `shot_id` off `nodes_json`, and JSONB round-trips unknown keys
   *  without validation, so no backend contract change is needed. */
  gen_task_id: string | null;
  /** Bound shot's owning scene (layout/focus use). */
  scene_id: string | null;
  /** Task 4 — set by `reconcileShotNodes` when this node's `shot_id` no
   *  longer resolves to a `script_shots` row (deleted from the storyboard
   *  list elsewhere). Renders a grey "removed" state; the editor stays
   *  read-only (PATCHing a gone shot would 404) until the orchestration
   *  layer drops the node from `nodes_json` on the next autosave — see
   *  `canvasCoreStore.ts`'s `doSave` stale-filter. Absent/undefined on
   *  every other node (including unbound drafts, which reconcile never
   *  touches). Final review Important 3: reversible — if the shot's
   *  `script_shots` row comes back (e.g. an Undo after an agent deletion)
   *  and the NEXT reconcile pass finds it in `shots` again while this node
   *  still carries `stale: true`, that same pass patches it back to
   *  `false` so the card returns to editable/saveable. Only ever flips
   *  `true`→`false` or `false`(absent)→`true` — never written when it was
   *  already absent/false, so a healthy node never gets a no-op `stale:
   *  false` patch. */
  stale?: boolean;
}

export interface PromptNodeData {
  body: string;
  /** Slug of the provider node-pack to invoke. Empty = not yet wired. */
  provider_slug: string;
  /** Snowflake AI library agent ID to ask, or null = use provider default. */
  agent_id: string | null;
  /** "idle" | "queued" | "running" | "succeeded" | "failed" | "blocked" —
   *  driven by the run protocol. "blocked" marks a downstream node that was
   *  not run because an upstream node failed.
   *  Persisted so a reload shows the last-known state. */
  run_status:
    | 'idle'
    | 'queued'
    | 'running'
    | 'succeeded'
    | 'failed'
    | 'blocked';
  run_started_at: string | null;
  run_finished_at: string | null;
  run_error: string | null;
  /**
   * Resources @-mentioned in the prompt body via the in-canvas picker.
   * Persisted with the node so the runner can forward them as context
   * attachments. Defaults to [] for nodes created before this field existed.
   */
  resource_refs: PromptResourceRef[];
  /**
   * Negative prompt text loaded from an asset's prompt library entry
   * (Phase 2). Optional; absent for hand-typed prompts. The generation
   * pipeline does not consume it yet — providers that support negative
   * prompts will pick it up when the runner grows that capability.
   */
  negative_body?: string;
  /**
   * Generation settings (Infinite parity G4-F1). Absent/null = a text
   * prompt (legacy behaviour). kind image/video routes Run through the
   * G4-B1 generation tasks instead of the text LLM.
   */
  gen?: PromptGenSettings | null;
  /** In-flight generation batch (P1-13) — task ids persisted at dispatch
   *  so a reload can resume polling (genResume). [] once the run settles. */
  gen_tasks?: Array<{ task_id: string; kind: 'image' | 'video' }>;
  /** @-selected input image url (IC parity ⑤ — the mention picker's
   *  「输入图」tab): overrides which wired input feeds i2i. Stale refs
   *  fall back to the first input (resolveEffectiveSourceUrl). */
  source_ref?: string;
  /** Manually attached reference images (IC's manualInputRefs, ⑨C):
   *  durable generated-media items picked from the library via the
   *  input-row's add key. Appended after wired inputs in the i2i chain. */
  manual_refs?: GeneratedImageRef[];
}

/** Image/video generation settings on a prompt node. */
export interface PromptGenSettings {
  kind: 'image' | 'video';
  /** mediahub_models catalog row name ('' = catalog default). */
  model: string;
  /** Image: aspect ratio preset (e.g. '16:9'). */
  ratio?: string;
  /** Image: batch count 1..8 (Infinite's cap). */
  count?: number;
  /** Video: aspect ratio (e.g. '16:9'). */
  aspect?: string;
  /** Image: quality knob (IC ⑨) — 'low'|'medium'|'high'; absent = Auto
   *  (provider default). Consumed by the codex chain; others ignore it. */
  quality?: string;
  /** Image: resolution ladder '1k'|'2k'|'4k' (IC 系统参数右列). Consumed
   *  by providers with a resolution knob (jimeng resolution_type); codex
   *  model sizes are fixed and ignore it. */
  resolution?: string;
  /** Video: clip length in seconds (IC 时长 pill) — jimeng CLI --duration;
   *  absent = provider default. */
  duration?: number;
}

export type OutputKind = 'text' | 'image' | 'video' | 'audio';

/** One generated media item on an output node (Infinite's node.images[]). */
export interface GeneratedImageRef {
  url: string;
  kind: OutputKind;
  name?: string;
}

/** Tag on loop-produced output slots — reused by (loop, round) on re-runs. */
export interface LoopSlotTag {
  loop_id: string;
  round_index: number;
}

/** User-uploaded media card (Infinite's 上传 node) — a SOURCE like shot,
 *  but its items are durable /generated-media/ URLs minted by the import
 *  endpoint, so connected prompts can use them as i2i/i2v sources. */
export interface MediaNodeData {
  title: string;
  items: GeneratedImageRef[];
  /** In-flight upload count — rendered as shimmer cells. */
  uploading?: number;
}

/** Standalone LLM node (IC 普通画布's LLM card, dual-canvas Phase 2.1):
 *  INPUT text (typed or wired from prompt/llm upstream) → run → OUTPUT
 *  text shown in-node. Self-contained — it does NOT spawn output nodes. */
export interface LlmNodeData {
  /** mediahub_models catalog row name ('' = catalog default). */
  provider_slug: string;
  /** AI-library agent id, or null = plain runner. */
  agent_id: string | null;
  input_text: string;
  output_text: string;
  run_status: 'idle' | 'running' | 'succeeded' | 'failed';
  run_error: string | null;
}

export interface GroupNodeData {
  /** User-editable caption shown in the container corner (②-3). */
  label?: string;
  /** Absorbed media (IC's smart-group `images[]`, group v2): dropping a
   *  media node onto the group swallows its items into this grid; the
   *  group then acts as an i2i source when wired into a prompt. Durable
   *  /generated-media/ URLs only. */
  items?: GeneratedImageRef[];
  /** In-flight direct-to-group uploads — shimmer cells in the grid. */
  uploading?: number;
}

export interface OutputNodeData {
  kind: OutputKind;
  /**
   * Generated media items (Infinite parity G4-F2) — count N lands N images
   * in ONE node. `preview_url` mirrors images[0] for the editor tooling.
   * Absent on legacy nodes (single preview_url behaviour).
   */
  images?: GeneratedImageRef[];
  /** In-flight generation placeholders (P0-3): cells still waiting for a
   *  result — rendered as shimmer skeletons so the canvas shows WHERE the
   *  images will land the moment the run is dispatched. */
  gen_pending?: number;
  /** Items of the current batch that failed (P0-3) — surfaced as a chip. */
  gen_failed?: number;
  /** Task ids whose POLL broke but whose backend task is still alive
   *  (P1-13) — rendered as a "task not lost" recover overlay with a
   *  Check Result re-query. */
  gen_recover?: string[];
  /** Set on history-archive nodes: the output node this archives for. */
  history_for?: string;
  /** Snowflake resource id that owns the rendered artifact, when one
   *  was persisted. Null for in-flight or text-only outputs. */
  resource_id: string | null;
  /** Inline preview text — used for kind='text' and as a fallback
   *  caption for media. */
  preview_text: string;
  /** URL of the rendered image/video/audio preview — populated when the
   *  run finishes and the artifact has a fetchable preview. Null until
   *  then. The crop editor reads from this. */
  preview_url: string | null;
  /** Last-committed crop applied to the image preview. Null = no crop
   *  (full image). Stored in normalized [0,1] coords so it survives
   *  rendering at any size. Only meaningful for kind='image'. */
  crop_region: CropRegion | null;
}

export interface LoopNodeData {
  mode: LoopMode;
  /** Optional human label, e.g. "for each shot". */
  label: string;
  /**
   * Batch fields (Infinite parity G3a). Optional so canvases persisted
   * before this slice keep loading — consumers default them via
   * `clampRounds(rounds ?? 1)` etc. (see loopVars.ts).
   */
  /** How many rounds a from-loop run executes (1..100, Infinite `count`). */
  rounds?: number;
  /** First 《计数》 value (≥1, Infinite `loopStart`). */
  round_start?: number;
  /** Rotating prompt list — round N uses entry (N-1) % length. */
  prompts?: string[];
  /** IC 图片 toggle — this loop takes an image group as input and slices
   *  it per round into the downstream generation's source. */
  image_input?: boolean;
  /** IC 提示词 toggle — this loop contributes a rotating prompt downstream. */
  show_prompt?: boolean;
  /** IC 批次 — images sliced from the upstream group per round, 1..100. */
  image_batch_size?: number;
}

// `T` defaults to `unknown` (Task 4 shotSync's `reconcileShotNodes` takes
// the canvas's opaque existing-node list as bare `SmartNode[]` — element
// data type unknown until narrowed by `type`/`shot_id`) rather than
// `Record<string, unknown>`: a default of `Record<string, unknown>` would
// make `SmartNode<ShotNodeData>` NOT assignable to bare `SmartNode[]`
// (`ShotNodeData` has no index signature), defeating the whole point of a
// default — every concrete `SmartNode<X>` is trivially assignable to
// `SmartNode<unknown>` instead.
export interface SmartNode<T = unknown> extends Record<string, unknown> {
  id: string;
  type: SmartNodeType;
  position: { x: number; y: number };
  data: T;
}

export type ShotNode = SmartNode<ShotNodeData>;
export type MediaNode = SmartNode<MediaNodeData>;
export type PromptNode = SmartNode<PromptNodeData>;
export type OutputNode = SmartNode<OutputNodeData>;
export type LoopNode = SmartNode<LoopNodeData>;
export type CharacterNode = SmartNode<CharacterNodeData>;
export type AnySmartNode =
  | ShotNode
  | MediaNode
  | PromptNode
  | OutputNode
  | LoopNode
  | CharacterNode;

/** Character canvas (kind='character'): the bible-card node the preset agent
 *  workflow hangs off. Binds a project_characters row when opened from the
 *  library; unbound (character_id null) when hand-placed. */
export interface CharacterNodeData {
  /** project_characters row id (snowflake string), or null = unbound. */
  character_id: string | null;
  name: string;
  role_tag: string;
  description: string;
  portrait_url: string | null;
}

/** Location/prop canvas (SP2): the library-card node the preset workflow
 *  hangs off. Binds a project_lib_entities row; unbound when hand-placed. */
export interface LibEntityNodeData {
  entity_id: string | null;
  name: string;
  badge_tag: string;
  description: string;
  cover_url: string | null;
}

/**
 * Connect-rule predicate for smart mode.
 *
 *   shot   → prompt          ✓
 *   shot   → loop            ✓ (loop fans out a shot collection)
 *   media  → prompt / loop   ✓ (uploaded media feeds generation as source)
 *   *      → media           ✗ (media cards are sources only)
 *   prompt → output          ✓
 *   prompt → prompt          ✓ (chain: one prompt feeds the next)
 *   prompt → loop            ✓ (prompt output can drive a loop)
 *   loop   → prompt          ✓ (loop iterates a prompt downstream)
 *   loop   → loop            ✓ (nested fanouts)
 *   shot   → output          ✗ (must go through a prompt)
 *   loop   → output          ✗ (always needs a prompt downstream)
 *   loop   → shot            ✗ (shots are sources)
 *   output → anything        ✗ (outputs are terminal)
 *   *      → shot            ✗ (shots are sources only)
 *   <unknown type>           allow — the surface is mode-aware, custom
 *                            future types opt in their own rules.
 */
export function canConnectSmart(
  sourceType: string | undefined,
  targetType: string | undefined,
): boolean {
  if (!sourceType || !targetType) return true;
  // Output stays a dead-end EXCEPT into a group — wiring a result into a
  // group collects its images (IC parity ⑦).
  if (sourceType === 'output') return targetType === 'group';
  if (targetType === 'shot') return false;
  // Media cards are sources like shot: they feed prompts/loops only.
  if (targetType === 'media') return false;
  // Group containers hold members via parentId — but as a COLLECTOR a
  // group accepts wires from image-bearing cards (media/output, IC ⑦):
  // connecting absorbs the source's images into the grid.
  if (targetType === 'group')
    return sourceType === 'media' || sourceType === 'output';
  if (
    sourceType === 'group' &&
    targetType !== 'prompt' &&
    targetType !== 'loop'
  )
    return false;
  // LLM cards take TEXT upstreams only, and feed text consumers.
  if (targetType === 'llm')
    return sourceType === 'prompt' || sourceType === 'llm';
  if (
    sourceType === 'llm' &&
    targetType !== 'prompt' &&
    targetType !== 'loop' &&
    targetType !== 'llm'
  )
    return false;
  if (sourceType === 'media' && targetType !== 'prompt' && targetType !== 'loop')
    return false;
  // Entity cards (character/location/prop) are SOURCE cards like shot:
  // they feed prompts only and take nothing.
  const ENTITY = new Set(['character', 'location', 'prop']);
  if (ENTITY.has(targetType)) return false;
  if (ENTITY.has(sourceType) && targetType !== 'prompt') return false;
  if (sourceType === 'shot' && targetType !== 'prompt' && targetType !== 'loop')
    return false;
  if (sourceType === 'loop' && (targetType === 'output' || targetType === 'shot'))
    return false;
  return true;
}

/** Default min-width per node type. Used by the renderer + factory. */
export const SMART_NODE_DEFAULT_WIDTH: Record<SmartNodeType, number> = {
  // Bumped from 240 (Task 3): the bound render packs 4 chips + a
  // description textarea + a 16:9 frame slot — 240 crowded the chip row.
  shot: 280,
  media: 240,
  llm: 300,
  prompt: 280,
  output: 260,
  loop: 340,
  timeline: 420,
  group: 300,
  character: 280,
  location: 280,
  prop: 280,
};

export const LOOP_MODE_TONE: Record<LoopMode, string> = {
  serial: 'border-canvas-line-strong/50',
  parallel: 'border-violet-500',
};

/** Run-status colour token for the prompt node halo + the output badge. */
export const RUN_STATUS_TONE: Record<PromptNodeData['run_status'], string> = {
  idle: 'border-canvas-line',
  queued: 'border-amber-400',
  running: 'border-indigo-500 animate-pulse',
  succeeded: 'border-emerald-500',
  failed: 'border-rose-500',
  // Dimmed neutral ink tone — "blocked" = skipped because an upstream node
  // failed, so it reads as inert/greyed-out, not as its own error.
  blocked: 'border-ink-400 opacity-60',
};

export function isSmartNode(node: CanvasNode): node is AnySmartNode {
  const obj = node as Record<string, unknown>;
  return (
    typeof obj.id === 'string' &&
    typeof obj.type === 'string' &&
    (obj.type === 'shot' ||
      obj.type === 'media' ||
      obj.type === 'prompt' ||
      obj.type === 'output' ||
      obj.type === 'loop')
  );
}
