// features/canvas-core/smart/promptInputs.ts
//
// Upstream image input resolution (G4-F3 — Infinite's cascade refs): a
// prompt fed by an output node holding a DURABLE generated image uses it
// as the generation source (i2i / i2v bridge, G4-B0). Only
// /api/v1/generated-media/ URLs qualify — the backend bridge can't fetch
// authenticated resource-file URLs, so external/preview links are skipped
// (resource_refs 打通 stays parked until a resource_id bridge exists).

import type { CanvasConnection, CanvasNode } from '../types';
import type { GeneratedImageRef, OutputNodeData } from './types';

const DURABLE_PREFIX = '/api/v1/generated-media/';

const asObj = (n: unknown) => n as Record<string, unknown>;

function durableImagesOf(node: CanvasNode): string[] {
  if (asObj(node).type !== 'output') return [];
  const data = (asObj(node).data ?? {}) as OutputNodeData;
  const refs: GeneratedImageRef[] = Array.isArray(data.images)
    ? (data.images as GeneratedImageRef[])
    : [];
  const candidates: Array<string | null | undefined> = [
    // Video refs are excluded: they are neither i2i sources nor <img>-able
    // compare underlays.
    ...refs.filter((i) => i.kind !== 'video').map((i) => i.url),
    data.preview_url,
  ];
  return candidates.filter(
    (url): url is string => typeof url === 'string' && url.startsWith(DURABLE_PREFIX),
  );
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
