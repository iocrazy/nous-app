/**
 * ClassicMode executor dispatch predicate (Phase 5a path B).
 *
 * Decides, for a single classic node, ONLY whether it RUNS or is PASSIVE —
 * purely from its `type`. It no longer resolves a provider_slug: the backend
 * `/api/v1/canvases/runs/classic-node` route is now the SINGLE SOURCE OF
 * DISPATCH TRUTH (`resolve_classic_dispatch` maps image_gen → generate_image,
 * video_gen → generate_video, comfy → nous/<slug>, llm → text adapter,
 * passive types → skipped, server-side). The old client-side provider_slug
 * MIRROR is gone — keeping it would mean two divergent dispatch tables.
 *
 * The cascade orchestrator (`cascade.ts`) layers graph-aware failure/blocked
 * propagation on top of this predicate; it only needs to know passive (skip,
 * never touch the network) vs runnable (hand the node to the runner) vs an
 * unmapped type (skip-vs-fail based on whether it has downstream dependents).
 */

/** Runnable AI-op node types — they POST to the classic-node run route and
 *  the BACKEND resolves how each one runs. */
const RUNNABLE_NODE_TYPES: ReadonlySet<string> = new Set([
  'llm',
  'comfy',
  'image_gen',
  'video_gen',
]);

/** Literal/sink/annotation/container node types: they hold data, collect or
 *  display results, or are pure-UI chrome — they never dispatch to a provider.
 *  In a cascade they are pass-through (skipped, stay idle). `text` is a static
 *  text literal (like `prompt`); `note` is a portless annotation; `preview` is
 *  a multi-modal display sink; `group` is a portless visual container.
 *  `video` (W3) is the video URL source counterpart of `image`. */
const PASSIVE_NODE_TYPES: ReadonlySet<string> = new Set([
  'image',
  'prompt',
  'text',
  'video',   // W3: static video URL source — passive, like `image`
  'output',
  'note',
  'preview',
  'group',
]);

/**
 * W3 transform node types — client-side data transformations that do NOT
 * dispatch to the backend. The cascade computes their EFFECTIVE data (applying
 * any piped input values) and records it so downstream nodes can read the
 * result via `nodeOutputValue`. They are counted as `skipped` in the cascade
 * report and do NOT trigger `onNodePatch`.
 *
 * Current member: `text_join` (concatenates two text inputs into one text
 * output). Why NOT loop/iterate or generator/batch: those require either a
 * `list` port type (not in the closed `CLASSIC_PORT_TYPES` set) or
 * re-executing downstream subgraphs per item — which would break the single
 * topo-sort-pass cascade model. They are deferred to Phase 6.
 */
const TRANSFORM_NODE_TYPES: ReadonlySet<string> = new Set([
  'text_join',
]);

/**
 * The dispatch decision for one classic node:
 *   - run       — a runnable AI-op node (llm / comfy / image_gen / video_gen).
 *                 The cascade hands it to the runner, which POSTs the node to
 *                 the backend; the backend resolves the route (and reports any
 *                 config error — e.g. comfy with no workflow_slug — in-band).
 *   - passive   — a literal/sink node: no provider dispatch; the cascade treats
 *                 it as a no-op pass-through (records original data).
 *   - transform — a client-side data transformation node (W3: `text_join`).
 *                 The cascade computes effective data (applying piped inputs)
 *                 and records it for downstream piping. No backend call.
 *   - unknown   — an unmapped node type. The cascade decides skip-vs-fail
 *                 based on whether the node has downstream dependents.
 */
export type ClassicDispatch =
  | { kind: 'run' }
  | { kind: 'passive' }
  | { kind: 'transform' }
  | { kind: 'unknown'; reason: string };

/**
 * Resolve how a classic node should run. Pure + deterministic — no graph,
 * no network, no React, no provider resolution (that's the backend's job now).
 */
export function dispatchClassicNode(
  nodeType: string | undefined,
): ClassicDispatch {
  if (nodeType && RUNNABLE_NODE_TYPES.has(nodeType)) {
    return { kind: 'run' };
  }

  if (nodeType && TRANSFORM_NODE_TYPES.has(nodeType)) {
    return { kind: 'transform' };
  }

  if (nodeType && PASSIVE_NODE_TYPES.has(nodeType)) {
    return { kind: 'passive' };
  }

  return {
    kind: 'unknown',
    reason: nodeType
      ? `classic node type '${nodeType}' has no provider mapping`
      : 'classic node is missing a type',
  };
}
