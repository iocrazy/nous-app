// features/canvas-core/smart/promptInputs.ts
//
// Upstream image input resolution (G4-F3 — Infinite's cascade refs): a
// prompt fed by an output node holding a DURABLE generated image uses it
// as the generation source (i2i / i2v bridge, G4-B0). Only
// /api/v1/generated-media/ URLs qualify — the backend bridge can't fetch
// authenticated resource-file URLs, so external/preview links are skipped
// (resource_refs 打通 stays parked until a resource_id bridge exists).

import type { CanvasConnection, CanvasNode } from '../types';
import type {
  GeneratedImageRef,
  GroupNodeData,
  MediaNodeData,
  OutputNodeData,
} from './types';

const DURABLE_PREFIX = '/api/v1/generated-media/';

const asObj = (n: unknown) => n as Record<string, unknown>;

function durableUrls(candidates: Array<string | null | undefined>): string[] {
  return candidates.filter(
    (url): url is string => typeof url === 'string' && url.startsWith(DURABLE_PREFIX),
  );
}

function durableImagesOf(node: CanvasNode): string[] {
  const type = asObj(node).type;
  if (type === 'media') {
    // Upload cards (the media node) mint durable URLs via the import
    // endpoint — same i2i eligibility as generated outputs.
    const data = (asObj(node).data ?? {}) as MediaNodeData;
    const items: GeneratedImageRef[] = Array.isArray(data.items) ? data.items : [];
    return durableUrls(items.filter((i) => i.kind !== 'video').map((i) => i.url));
  }
  if (type === 'group') {
    // Groups with absorbed media (group v2) source their grid like a
    // media card does.
    const data = (asObj(node).data ?? {}) as GroupNodeData;
    const items: GeneratedImageRef[] = Array.isArray(data.items) ? data.items : [];
    return durableUrls(items.filter((i) => i.kind !== 'video').map((i) => i.url));
  }
  if (type !== 'output') return [];
  const data = (asObj(node).data ?? {}) as OutputNodeData;
  const refs: GeneratedImageRef[] = Array.isArray(data.images)
    ? (data.images as GeneratedImageRef[])
    : [];
  return durableUrls([
    // Video refs are excluded: they are neither i2i sources nor <img>-able
    // compare underlays.
    ...refs.filter((i) => i.kind !== 'video').map((i) => i.url),
    data.preview_url,
  ]);
}

/** Every durable generated image feeding into a prompt, in connection
 *  order, deduped — the multi-source compare list (P1-4). */
export function resolveSourceUrls(
  promptId: string,
  nodes: CanvasNode[],
  connections: CanvasConnection[],
): string[] {
  const byId = new Map(nodes.map((n) => [String(asObj(n).id), n]));
  const seen = new Set<string>();
  const urls: string[] = [];
  for (const c of connections) {
    if (String(c.target) !== promptId) continue;
    const upstream = byId.get(String(c.source));
    if (!upstream) continue;
    for (const url of durableImagesOf(upstream)) {
      if (seen.has(url)) continue;
      seen.add(url);
      urls.push(url);
    }
  }
  // Manually attached references (IC's manualInputRefs) ride AFTER the
  // wired inputs, deduped against them.
  const self = byId.get(promptId);
  const manual = ((asObj(self ?? {}).data ?? {}) as {
    manual_refs?: GeneratedImageRef[];
  }).manual_refs;
  for (const url of durableUrls(
    (manual ?? []).filter((i) => i.kind !== 'video').map((i) => i.url),
  )) {
    if (seen.has(url)) continue;
    seen.add(url);
    urls.push(url);
  }
  return urls;
}

/** First durable generated image feeding into a prompt, or null. */
export function resolveSourceUrl(
  promptId: string,
  nodes: CanvasNode[],
  connections: CanvasConnection[],
): string | null {
  return resolveSourceUrls(promptId, nodes, connections)[0] ?? null;
}


/** The i2i source for a prompt, honoring an @-selected input image (IC
 *  parity ⑤): ``data.source_ref`` wins while it is still one of the wired
 *  inputs; a stale ref (upstream image deleted/replaced) falls back to the
 *  first input rather than silently generating from nothing. */
export function resolveEffectiveSourceUrl(
  prompt: CanvasNode,
  nodes: CanvasNode[],
  connections: CanvasConnection[],
): string | null {
  const promptId = String(asObj(prompt).id);
  const inputs = resolveSourceUrls(promptId, nodes, connections);
  if (inputs.length === 0) return null;
  const ref = (asObj(prompt).data as { source_ref?: string } | undefined)
    ?.source_ref;
  if (ref && inputs.includes(ref)) return ref;
  return inputs[0];
}


/** Upstream prompt text preview (IC's inputPromptPreview, ⑨C): non-empty
 *  bodies of wired upstream prompt/llm cards, joined for a one-line hint. */
export function upstreamPromptText(
  promptId: string,
  nodes: CanvasNode[],
  connections: CanvasConnection[],
): string {
  const byId = new Map(nodes.map((n) => [String(asObj(n).id), n]));
  const parts: string[] = [];
  for (const c of connections) {
    if (String(c.target) !== promptId) continue;
    const upstream = byId.get(String(c.source));
    if (!upstream) continue;
    const type = asObj(upstream).type;
    if (type !== 'prompt' && type !== 'llm') continue;
    const body = String(
      ((asObj(upstream).data ?? {}) as { body?: string }).body ?? '',
    ).trim();
    if (body) parts.push(body);
  }
  return parts.join(' · ');
}


/** All inputs with the @-selected one moved to the FRONT (multi-ref i2i —
 *  IC 图1/图2: every input ships as a reference; the chosen one leads). */
export function resolveEffectiveSourceUrls(
  prompt: CanvasNode,
  nodes: CanvasNode[],
  connections: CanvasConnection[],
): string[] {
  const promptId = String(asObj(prompt).id);
  const inputs = resolveSourceUrls(promptId, nodes, connections);
  const ref = (asObj(prompt).data as { source_ref?: string } | undefined)
    ?.source_ref;
  if (ref && inputs.includes(ref)) {
    return [ref, ...inputs.filter((u) => u !== ref)];
  }
  return inputs;
}
