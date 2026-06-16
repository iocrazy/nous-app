/**
 * ClassicMode data piping (Phase 5a).
 *
 * ComfyUI-style canvases are USELESS unless an upstream node's OUTPUT flows
 * into the downstream node's INPUT. The cascade (`cascade.ts`) processes nodes
 * in topological order; before it runs (or resolves) a node it asks THIS module
 * two pure questions:
 *
 *   1. `nodeOutputValue(srcType, srcHandle, srcData, srcRunResult)` — what
 *      string does the upstream node EMIT on the wired output handle? For a
 *      passive SOURCE node (prompt / text / image) the value comes from its
 *      `data`; for a RUNNABLE node it comes from its `run_result`.
 *
 *   2. `inputParamKey(dstType, dstHandle)` — which `data` key on the downstream
 *      node does that piped value FILL? These keys are exactly the ones the
 *      backend (`canvas_run_service._extract_image_gen_params` /
 *      `_extract_video_gen_params`) reads, so injecting them into the effective
 *      `data` makes piping work transparently — no backend change needed.
 *
 * Both maps are PURE + deterministic — no graph, no React, no network. The
 * cascade composes them: for each incoming wire it resolves the source's output
 * value and the target's param key, then writes the value onto the target's
 * EFFECTIVE data (a per-run copy — the persisted node `data` is never mutated).
 * A connected input takes PRECEDENCE over the node's own widget value (ComfyUI
 * semantics).
 */

/** Coerce an arbitrary value to a non-undefined string, else undefined.
 *  Output values that aren't strings (or are absent) don't pipe. */
function asPipedString(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined;
}

/**
 * What an upstream node EMITS on a given output handle, or `undefined` when the
 * (nodeType, handle) pair carries no value or the underlying field is absent /
 * non-string.
 *
 * Sources read from `data`:
 *   - prompt    `prompt-out`  → `data.prompt`
 *   - text      `text-out`    → `data.text`
 *   - image     `image-out`   → `data.image_url ?? data.imageUrl`
 *   - video     `video-out`   → `data.video_url`  (W3: video URL source)
 *   - text_join `text-out`    → `[data.text_a, data.text_b].join(sep)` (W3 transform)
 *
 * Runnables read from `runResult`:
 *   - image_gen `image-out` → `runResult.image_url`
 *   - llm       `text-out`  → `runResult.text`
 *   - comfy     `image-out` → `runResult.image_url`
 *   - comfy     `text-out`  → `runResult.text`
 *   - video_gen `video-out` → `runResult.video_url`
 */
export function nodeOutputValue(
  nodeType: string | undefined,
  outputHandleId: string | null | undefined,
  data: Record<string, unknown>,
  runResult: Record<string, unknown> | null | undefined,
): string | undefined {
  if (!nodeType || !outputHandleId) return undefined;
  const rr = runResult ?? {};

  switch (nodeType) {
    // --- passive sources: value lives in the node's own data ---
    case 'prompt':
      return outputHandleId === 'prompt-out' ? asPipedString(data.prompt) : undefined;
    case 'text':
      return outputHandleId === 'text-out' ? asPipedString(data.text) : undefined;
    case 'image':
      return outputHandleId === 'image-out'
        ? asPipedString(data.image_url ?? data.imageUrl)
        : undefined;
    // W3: video source — value lives in data.video_url (passive like `image`)
    case 'video':
      return outputHandleId === 'video-out' ? asPipedString(data.video_url) : undefined;

    // W3: text_join transform — computed from effective data (text_a + sep + text_b).
    // The cascade records the EFFECTIVE data for this node (not just stored data),
    // so piped values from text-a-in / text-b-in are already folded into `data`
    // by the time `nodeOutputValue` is called by a downstream node.
    case 'text_join': {
      if (outputHandleId !== 'text-out') return undefined;
      const sep = typeof data.separator === 'string' ? data.separator : ' ';
      const parts = [data.text_a, data.text_b]
        .filter((v) => typeof v === 'string' && v.length > 0) as string[];
      return parts.length > 0 ? parts.join(sep) : undefined;
    }

    // --- runnables: value lives in the node's run_result ---
    case 'image_gen':
      return outputHandleId === 'image-out' ? asPipedString(rr.image_url) : undefined;
    case 'llm':
      return outputHandleId === 'text-out' ? asPipedString(rr.text) : undefined;
    case 'comfy':
      if (outputHandleId === 'image-out') return asPipedString(rr.image_url);
      if (outputHandleId === 'text-out') return asPipedString(rr.text);
      return undefined;
    case 'video_gen':
      return outputHandleId === 'video-out' ? asPipedString(rr.video_url) : undefined;

    default:
      return undefined;
  }
}

/**
 * Which `data` key on the downstream node a piped value FILLS, or `null` when
 * the (nodeType, handle) pair takes no run param. The keys match exactly what
 * the backend run service reads out of `node.data`:
 *
 *   - image_gen: `prompt-in`  → `prompt`, `image-in`  → `reference_image_url`
 *   - video_gen: `image-in`   → `source_image_url`, `prompt-in` → `prompt`
 *   - llm:       `prompt-in`  → `prompt`, `image-in`  → `reference_image_url`,
 *                `text-in`    → `prompt`  (a static-text source feeds the prompt;
 *                                          body derivation then picks it up)
 *   - comfy:     `prompt-in`  → `prompt`, `image-in`  → `reference_image_url`
 *   - text_join: `text-a-in` → `text_a`, `text-b-in` → `text_b`  (W3 transform)
 *   - sources (prompt/text/image/video) and sinks (output/preview/note/group) → null
 */
export function inputParamKey(
  nodeType: string | undefined,
  inputHandleId: string | null | undefined,
): string | null {
  if (!nodeType || !inputHandleId) return null;

  switch (nodeType) {
    case 'image_gen':
      if (inputHandleId === 'prompt-in') return 'prompt';
      if (inputHandleId === 'image-in') return 'reference_image_url';
      return null;
    case 'video_gen':
      if (inputHandleId === 'image-in') return 'source_image_url';
      if (inputHandleId === 'prompt-in') return 'prompt';
      return null;
    case 'llm':
      if (inputHandleId === 'prompt-in') return 'prompt';
      if (inputHandleId === 'text-in') return 'prompt';
      if (inputHandleId === 'image-in') return 'reference_image_url';
      return null;
    case 'comfy':
      if (inputHandleId === 'prompt-in') return 'prompt';
      if (inputHandleId === 'image-in') return 'reference_image_url';
      return null;
    // W3 transform: text_join maps its two inputs to data keys the cascade
    // and `nodeOutputValue` read when computing the joined output.
    case 'text_join':
      if (inputHandleId === 'text-a-in') return 'text_a';
      if (inputHandleId === 'text-b-in') return 'text_b';
      return null;
    default:
      return null;
  }
}

/** A node's recorded output, captured by the cascade as it walks topo order so
 *  downstream nodes can read it. Sources record `data` (runResult null);
 *  runnables record `runResult` after they finish. */
export interface RecordedOutput {
  nodeType: string | undefined;
  data: Record<string, unknown>;
  runResult: Record<string, unknown> | null;
}

/** One incoming wire into a node, carrying the resolved handle ids. */
export interface IncomingWire {
  sourceId: string;
  sourceHandle: string | null;
  targetHandle: string | null;
}

/**
 * Build the EFFECTIVE data for a node about to run: a shallow copy of its own
 * data, with each resolved incoming wire's value written onto the mapped param
 * key. A connected input OVERRIDES the node's own widget value (ComfyUI
 * semantics). When two wires target the same input, the LAST one in `incoming`
 * order wins (deterministic by edge order).
 *
 * The input `ownData` is never mutated — a fresh object is returned.
 */
export function buildEffectiveData(
  nodeType: string | undefined,
  ownData: Record<string, unknown>,
  incoming: readonly IncomingWire[],
  outputs: ReadonlyMap<string, RecordedOutput>,
): Record<string, unknown> {
  const effective: Record<string, unknown> = { ...ownData };
  for (const wire of incoming) {
    const src = outputs.get(wire.sourceId);
    if (!src) continue;
    const value = nodeOutputValue(src.nodeType, wire.sourceHandle, src.data, src.runResult);
    if (value === undefined) continue;
    const key = inputParamKey(nodeType, wire.targetHandle);
    if (!key) continue;
    effective[key] = value; // piped value takes precedence over own widget value
  }
  return effective;
}
