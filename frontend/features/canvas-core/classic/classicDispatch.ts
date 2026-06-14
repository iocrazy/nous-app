/**
 * ClassicMode executor dispatch table (Phase 5a B5).
 *
 * Decides, for a single classic node, HOW it runs — purely from its
 * `type` + opaque `data` payload, with NO knowledge of the surrounding
 * graph. The cascade orchestrator (`cascade.ts`) layers the graph-aware
 * failure/blocked propagation on top.
 *
 * This is the FRONTEND mirror of the backend C2 resolver
 * (`backend/app/services/canvas/classic_dispatch.py::resolve_provider_slug`).
 * We resolve the provider_slug client-side so the cascade can reuse the
 * EXISTING synchronous canvas run transport (POST /canvases/runs/prompts —
 * see `classicRunner.ts`) without inventing a new endpoint: comfy nodes
 * resolve to `nous/<workflow_slug>`, which the existing nous/ route
 * block-polls to a terminal state (single-synchronous, no DBOS).
 *
 * Keep the key lists + precedence byte-aligned with the backend so a node
 * routes identically whichever side resolves it.
 */

/** Classic node type keys — mirror `classic/registry.ts`. */
const NODE_TYPE_LLM = 'llm';
const NODE_TYPE_COMFY = 'comfy';

/** Literal/sink/annotation node types: they hold data, collect results, or
 *  are pure-UI stickies — they never dispatch to a provider. In a cascade
 *  they are pass-through (skipped, stay idle). `text` is a static text
 *  literal (like `prompt`); `note` is a portless annotation. */
const PASSIVE_NODE_TYPES: ReadonlySet<string> = new Set([
  'image',
  'prompt',
  'text',
  'output',
  'note',
]);

/** Data keys accepted for an llm node's model / provider override
 *  (snake + camel + bare model), in precedence order — mirror of the
 *  backend `_LLM_PROVIDER_KEYS`. */
const LLM_PROVIDER_KEYS = ['provider_slug', 'providerSlug', 'model'] as const;
/** Data keys accepted for a comfy node's workflow slug — mirror of the
 *  backend `_COMFY_WORKFLOW_KEYS`. */
const COMFY_WORKFLOW_KEYS = ['workflow_slug', 'workflowSlug'] as const;

/**
 * The dispatch decision for one classic node:
 *   - run     — a runnable node (llm / comfy-with-slug). `providerSlug` is
 *               the slug the run transport expects (null = default model).
 *   - passive — a literal/sink node (image / prompt / output): no provider
 *               dispatch; the cascade treats it as a no-op pass-through.
 *   - invalid — a runnable TYPE that cannot run as configured (comfy with
 *               no workflow_slug). A CONTAINED failure, never silent.
 *   - unknown — an unmapped node type. The cascade decides skip-vs-fail
 *               based on whether the node has downstream dependents.
 */
export type ClassicDispatch =
  | { kind: 'run'; providerSlug: string | null }
  | { kind: 'passive' }
  | { kind: 'invalid'; reason: string }
  | { kind: 'unknown'; reason: string };

function firstNonBlankString(
  data: Record<string, unknown>,
  keys: ReadonlyArray<string>,
): string | null {
  for (const key of keys) {
    const value = data[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return null;
}

/**
 * Resolve how a classic node should run. Pure + deterministic — no graph,
 * no network, no React.
 */
export function dispatchClassicNode(
  nodeType: string | undefined,
  data: Record<string, unknown> | undefined,
): ClassicDispatch {
  const payload = data ?? {};

  if (nodeType === NODE_TYPE_LLM) {
    return { kind: 'run', providerSlug: firstNonBlankString(payload, LLM_PROVIDER_KEYS) };
  }

  if (nodeType === NODE_TYPE_COMFY) {
    const workflowSlug = firstNonBlankString(payload, COMFY_WORKFLOW_KEYS);
    if (!workflowSlug) {
      return {
        kind: 'invalid',
        reason:
          'comfy node is missing a workflow_slug in its data; cannot route to nous-center',
      };
    }
    return { kind: 'run', providerSlug: `nous/${workflowSlug}` };
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
