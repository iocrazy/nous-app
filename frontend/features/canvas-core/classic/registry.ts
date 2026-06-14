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
export const CLASSIC_PORT_TYPES = ['image', 'text', 'prompt'] as const;

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
  ],
  outputs: [{ id: 'text-out', type: 'text' }],
};

const outputNode: ClassicNodeDefinition = {
  type: 'output',
  label: 'Output',
  inputs: [{ id: 'image-in', type: 'image' }],
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

export const classicNodeDefinitions: Record<string, ClassicNodeDefinition> = {
  image: imageNode,
  prompt: promptNode,
  llm: llmNode,
  output: outputNode,
  comfy: comfyNode,
};

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
