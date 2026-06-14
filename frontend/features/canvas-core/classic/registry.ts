/**
 * ClassicMode typed-port registry (Phase 5a B2).
 *
 * ComfyUI-style node canvas. Unlike SmartMode — where a node has a single
 * boolean source/target handle — ClassicMode nodes expose MULTIPLE TYPED
 * ports. Each port carries a port `type` (image / text / prompt) and an
 * `id` that is the React Flow handle id. A wire is valid only when the
 * source node's OUTPUT port type matches the target node's INPUT port type.
 *
 * Conceptually modelled on the storyboard node registry's `connectivity`
 * pattern (`features/storyboard/domain/nodeRegistry.ts`), but this is a
 * standalone DATA registry — no React, no imports from storyboard.
 *
 * The MVP node views that render these ports land in a LATER task (B4);
 * this module is pure data + the connect rule so it stays unit-testable.
 */

/** The closed set of ClassicMode port types. */
export const CLASSIC_PORT_TYPES = ['image', 'text', 'prompt', 'video'] as const;

export type ClassicPortType = (typeof CLASSIC_PORT_TYPES)[number];

/**
 * A single typed port on a classic node. `id` is the React Flow handle
 * id — it must be unique within the node so a wire can address exactly
 * one port on each end.
 */
export interface ClassicPort {
  id: string;
  type: ClassicPortType;
}

/** Static definition of a ClassicMode node type. */
export interface ClassicNodeDefinition {
  /** Node type key — also the React Flow node `type`. */
  type: string;
  /** Human label shown in the (placeholder) node view + menus. */
  label: string;
  /** Typed input ports (data flowing IN). */
  inputs: ClassicPort[];
  /** Typed output ports (data flowing OUT). */
  outputs: ClassicPort[];
}

const imageNode: ClassicNodeDefinition = {
  type: 'image',
  label: 'Image',
  inputs: [],
  outputs: [{ id: 'image-out', type: 'image' }],
};

const promptNode: ClassicNodeDefinition = {
  type: 'prompt',
  label: 'Prompt',
  inputs: [],
  outputs: [{ id: 'prompt-out', type: 'prompt' }],
};

const llmNode: ClassicNodeDefinition = {
  type: 'llm',
  label: 'LLM',
  inputs: [
    { id: 'prompt-in', type: 'prompt' },
    // optional image conditioning
    { id: 'image-in', type: 'image' },
    // optional static-text conditioning (fed by a `text` source node)
    { id: 'text-in', type: 'text' },
  ],
  outputs: [{ id: 'text-out', type: 'text' }],
};

const textNode: ClassicNodeDefinition = {
  type: 'text',
  label: 'Text',
  // A static text literal — like `prompt`/`image`, it is a passive data
  // source with no inputs and a single typed output.
  inputs: [],
  outputs: [{ id: 'text-out', type: 'text' }],
};

const noteNode: ClassicNodeDefinition = {
  type: 'note',
  label: 'Note',
  // A pure-UI sticky annotation (standard ComfyUI "Note"): zero ports, so
  // it can never participate in a wire.
  inputs: [],
  outputs: [],
};

const outputNode: ClassicNodeDefinition = {
  type: 'output',
  label: 'Output',
  inputs: [{ id: 'image-in', type: 'image' }],
  outputs: [],
};

const previewNode: ClassicNodeDefinition = {
  type: 'preview',
  label: 'Preview',
  // A display sink (like `output`, but multi-modal): it DISPLAYS whatever is
  // wired in. THREE typed inputs — image, text, and video — and ZERO outputs,
  // so nothing flows out of it. The `video` input is the consumer for a
  // `video_gen` node's `video-out`, so an animated result is no longer a
  // dead-end. Passive (see `classicDispatch.ts`).
  inputs: [
    { id: 'image-in', type: 'image' },
    { id: 'text-in', type: 'text' },
    { id: 'video-in', type: 'video' },
  ],
  outputs: [],
};

const groupNode: ClassicNodeDefinition = {
  type: 'group',
  label: 'Group',
  // A pure-UI visual container/frame (standard ComfyUI "Group"): zero ports,
  // like `note`, so it can never participate in a wire.
  inputs: [],
  outputs: [],
};

const comfyNode: ClassicNodeDefinition = {
  type: 'comfy',
  label: 'ComfyUI',
  inputs: [
    { id: 'prompt-in', type: 'prompt' },
    { id: 'image-in', type: 'image' },
  ],
  // A comfy graph can emit BOTH an image and a text artifact on distinct
  // handles — the connect rule resolves the right one by handle id.
  outputs: [
    { id: 'image-out', type: 'image' },
    { id: 'text-out', type: 'text' },
  ],
};

const imageGenNode: ClassicNodeDefinition = {
  type: 'image_gen',
  label: 'Image Gen',
  // A RUNNABLE AI op: turns a prompt (+ optional reference image) into a
  // freshly generated image. The backend resolves it to `generate_image`.
  inputs: [
    { id: 'prompt-in', type: 'prompt' },
    // optional reference / conditioning image
    { id: 'image-in', type: 'image' },
  ],
  outputs: [{ id: 'image-out', type: 'image' }],
};

const videoGenNode: ClassicNodeDefinition = {
  type: 'video_gen',
  label: 'Video Gen',
  // A RUNNABLE AI op: animates a source image (+ optional prompt) into a
  // video. The backend resolves it to `generate_video`. Its output is the
  // new `video` port type.
  inputs: [
    { id: 'image-in', type: 'image' },
    // optional motion / style prompt
    { id: 'prompt-in', type: 'prompt' },
  ],
  outputs: [{ id: 'video-out', type: 'video' }],
};

/**
 * W3: Video source node.
 *
 * A static video URL literal — the `video` counterpart of `image`. It is a
 * PASSIVE source (no backend call): the user pastes a URL into `data.video_url`
 * and it flows downstream on the `video-out` handle to a consumer such as
 * `preview`. Zero inputs, one video output.
 */
const videoNode: ClassicNodeDefinition = {
  type: 'video',
  label: 'Video',
  inputs: [],
  outputs: [{ id: 'video-out', type: 'video' }],
};

/**
 * W3: Text Join transform node.
 *
 * Concatenates two text inputs into a single text output client-side —
 * no backend call required. This fills a real graph-composition gap: without
 * it there is no way to merge two text streams before feeding an LLM or
 * image-gen prompt.
 *
 * Dispatch kind: `transform` (see `classicDispatch.ts`) — the cascade
 * computes the effective data (applying piped values from `text-a-in` and
 * `text-b-in`), then records the joined text for downstream piping. The
 * separator defaults to a single space; set `data.separator` to override.
 *
 * Why NOT loop/iterate or batch?
 *   - No `list` port type exists in the closed `CLASSIC_PORT_TYPES` set.
 *   - The cascade processes a DAG in a single topo-sort pass (each node once).
 *   - Fan-out execution (running downstream branches N times per list item)
 *     would require rearchitecting the cascade loop — not just extending it.
 *   - Those patterns are deferred to Phase 6 (async/DBOS execution model).
 */
const textJoinNode: ClassicNodeDefinition = {
  type: 'text_join',
  label: 'Text Join',
  inputs: [
    { id: 'text-a-in', type: 'text' },
    { id: 'text-b-in', type: 'text' },
  ],
  outputs: [{ id: 'text-out', type: 'text' }],
};

export const classicNodeDefinitions: Record<string, ClassicNodeDefinition> = {
  image: imageNode,
  prompt: promptNode,
  text: textNode,
  llm: llmNode,
  output: outputNode,
  comfy: comfyNode,
  note: noteNode,
  preview: previewNode,
  group: groupNode,
  image_gen: imageGenNode,
  video_gen: videoGenNode,
  // W3 additions
  video: videoNode,
  text_join: textJoinNode,
};

/**
 * Every classic node definition as an ORDERED, iterable list — so menus /
 * palettes can enumerate the full node set without depending on JS object
 * key order. Grouped by role: passive SOURCES first, then RUNNABLE ops,
 * then SINKS / structural nodes.
 */
export const CLASSIC_NODE_DEFINITIONS: readonly ClassicNodeDefinition[] = [
  // passive sources
  imageNode,
  promptNode,
  textNode,
  videoNode,         // W3: video URL source (feeds preview.video-in)
  // data transforms (client-side, no backend call)
  textJoinNode,      // W3: concatenate two text inputs
  // runnable AI-ops
  llmNode,
  comfyNode,
  imageGenNode,
  videoGenNode,
  // sinks / structural
  outputNode,
  previewNode,
  noteNode,
  groupNode,
];

export function getClassicNodeDefinition(
  type: string | undefined,
): ClassicNodeDefinition | undefined {
  if (!type) return undefined;
  return classicNodeDefinitions[type];
}

function findPort(ports: ClassicPort[], handleId: string): ClassicPort | undefined {
  return ports.find((p) => p.id === handleId);
}

/**
 * ClassicMode connect rule. Resolves the source node's OUTPUT port by
 * `srcHandle` and the target node's INPUT port by `tgtHandle`, then
 * requires their port TYPES to match.
 *
 * Rejects (returns false) when:
 *   - either node type is unknown
 *   - either handle id is missing / not found among the relevant ports
 *     (an OUTPUT handle used as an input target, or vice-versa, is "not
 *     found" on that side and therefore rejected)
 *   - the resolved port types differ
 *
 * Self-loop rejection (source node === target node) lives in
 * `validateCanvasConnection`, which has the node IDS; this predicate only
 * sees node TYPES + handle ids.
 */
export function canConnectClassic(
  srcType: string | undefined,
  tgtType: string | undefined,
  srcHandle: string | null | undefined,
  tgtHandle: string | null | undefined,
): boolean {
  if (!srcHandle || !tgtHandle) return false;
  const srcDef = getClassicNodeDefinition(srcType);
  const tgtDef = getClassicNodeDefinition(tgtType);
  if (!srcDef || !tgtDef) return false;

  const srcPort = findPort(srcDef.outputs, srcHandle);
  const tgtPort = findPort(tgtDef.inputs, tgtHandle);
  if (!srcPort || !tgtPort) return false;

  return srcPort.type === tgtPort.type;
}
