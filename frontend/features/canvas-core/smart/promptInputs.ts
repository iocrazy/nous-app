// features/canvas-core/smart/promptInputs.ts
//
// Upstream image input resolution (G4-F3 — Infinite's cascade refs): a
// prompt fed by an output node holding a DURABLE generated image uses it
// as the generation source (i2i / i2v bridge, G4-B0).
//
// TWO url families qualify, and the backend resolves both (asset-library P4
// Task 3, `generated_media_service.classify_reference_url`):
//   /api/v1/generated-media/{id}/(cover|stream|file) — an earlier generation
//   /api/v1/resources/{id}/(cover|file)              — a library file, e.g.
//                                                      an asset's reference
// Everything else (external links, blob/data previews) is skipped here.
//
// A url that passes this filter is not promised a picture: the backend still
// scope-checks a resource and can find no image behind it. Those come back as
// `dropped_refs` on the run and are shown on the node — the filter is a
// cheap first pass, never the last word.

import {
  fetchBundle,
  type AssetBundle,
  type DroppedReference,
} from '../../../services/assetsService';
import type { CanvasConnection, CanvasNode } from '../types';
import type {
  AssetNodeData,
  GeneratedImageRef,
  GroupNodeData,
  MediaNodeData,
  OutputNodeData,
} from './types';

export const DURABLE_PREFIXES = [
  '/api/v1/generated-media/',
  '/api/v1/resources/',
] as const;

const asObj = (n: unknown) => n as Record<string, unknown>;

function durableUrls(candidates: Array<string | null | undefined>): string[] {
  return candidates.filter(
    (url): url is string =>
      typeof url === 'string' && DURABLE_PREFIXES.some((p) => url.startsWith(p)),
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


// ─── Asset-library composition (P4 Task 5) ──────────────────────────────────
//
// An `asset` card wired into a prompt contributes THREE things, and the backend
// decides how much of each survives:
//
//   references — the card's checked files, as `/api/v1/resources/{id}/cover`
//                URLs. They lead `source_urls`, ahead of the wired i2i inputs:
//                a subject reference is what the picture is OF, and the
//                provider's reference ceiling trims from the tail.
//   positive   — the bundle's composed prompt text, prefixed to the body.
//   negative   — sent as `params.negative`. Whether the provider accepts one is
//                NOT decided here: `GenerationRequest.reconcile` drops it and
//                names `negative` in `dropped_knobs`, which the node already
//                renders. Guessing client-side would be a second answer to a
//                question the backend already answers authoritatively.
//
// The bundle is fetched per RUN rather than cached on the node, because it
// depends on the model — the same card bundles nine references for codex and
// none for ark — and the model is a knob the user changes between runs.

/** One asset card's contribution, plus what went wrong asking for it. */
export interface AssetInputContribution {
  /** The asset NODE's id, so a report lands on the card that caused it. */
  nodeId: string;
  assetId: string;
  /** Reference URLs, bundle order, already intersected with the selection. */
  urls: string[];
  positive: string;
  negative: string;
  /** References the bundle would not send, with the backend's reason. */
  dropped: DroppedReference[];
  /** The bundle request itself failed — NOT the same as "nothing dropped". */
  error: string | null;
}

/** What upstream assets add to one prompt's generation request. */
export interface ComposedAssetInputs {
  /** `/api/v1/resources/{id}/cover`, upstream order then bundle order, deduped. */
  reference_urls: string[];
  /** Newline-joined positives, in upstream order. '' when there are none. */
  prompt_prefix: string;
  /** Newline-joined negatives, deduped. '' when there are none. */
  negative: string;
  /** Per contributing card, for the badge — always present, even when clean. */
  contributions: AssetInputContribution[];
}

export const EMPTY_ASSET_INPUTS: ComposedAssetInputs = {
  reference_urls: [],
  prompt_prefix: '',
  negative: '',
  contributions: [],
};

/** The reference shape the backend's resource bridge accepts (T3). Relative on
 *  purpose: `classify_reference_url` takes an absolute URL only when the host is
 *  ours, and every producer in this repo emits a path. */
export function assetReferenceUrl(resourceId: string): string {
  return `/api/v1/resources/${resourceId}/cover`;
}

/** The asset cards feeding a prompt directly (ONE hop), in connection order. */
export function upstreamAssetNodes(
  promptId: string,
  nodes: CanvasNode[],
  connections: CanvasConnection[],
): Array<{ nodeId: string; data: AssetNodeData }> {
  const byId = new Map(nodes.map((n) => [String(asObj(n).id), n]));
  const seen = new Set<string>();
  const out: Array<{ nodeId: string; data: AssetNodeData }> = [];
  for (const c of connections) {
    if (String(asObj(c).target) !== promptId) continue;
    const nodeId = String(asObj(c).source);
    if (seen.has(nodeId)) continue;
    seen.add(nodeId);
    const node = byId.get(nodeId);
    if (!node || String(asObj(node).type ?? '') !== 'asset') continue;
    const data = (asObj(node).data ?? {}) as AssetNodeData;
    // A tombstoned card has no asset row left to bundle; it keeps rendering its
    // snapshot but contributes nothing, which is what the tombstone says.
    if (data.removed === true) continue;
    if (!data.asset_id) continue;
    out.push({ nodeId, data });
  }
  return out;
}

/**
 * Compose what the upstream asset cards hand this prompt's run.
 *
 * `scopeId` is required and must be non-empty: `/api/v1/assets` is scoped per
 * request and an empty `scope_id` is a 403 `not_a_member`, not an unscoped
 * query. With no scope the cards are reported as failed rather than skipped —
 * a run that silently shipped no references would look identical to one whose
 * asset had none.
 *
 * A failed bundle fetch fails only ITS card. One unreachable asset must not
 * throw away the references of the card beside it, and it must not be
 * swallowed either: it lands in that card's `error`.
 */
export async function resolveAssetInputs(
  promptId: string,
  nodes: CanvasNode[],
  connections: CanvasConnection[],
  opts: { model: string; scopeId: string; fetch?: typeof fetchBundle },
): Promise<ComposedAssetInputs> {
  const cards = upstreamAssetNodes(promptId, nodes, connections);
  if (cards.length === 0) return EMPTY_ASSET_INPUTS;

  const call = opts.fetch ?? fetchBundle;
  const contributions = await Promise.all(
    cards.map(async ({ nodeId, data }): Promise<AssetInputContribution> => {
      const base: AssetInputContribution = {
        nodeId,
        assetId: String(data.asset_id),
        urls: [],
        positive: '',
        negative: '',
        dropped: [],
        error: null,
      };
      if (!opts.scopeId) {
        return { ...base, error: 'no_scope' };
      }
      let bundle: AssetBundle;
      try {
        bundle = await call(opts.scopeId, String(data.asset_id), {
          model: opts.model,
          loadoutId: data.loadout_id ?? undefined,
          // The card's checklist goes TO the endpoint, which trims the
          // provider's ceiling within it. See the note below the call.
          selectedFileIds: data.selected_file_ids ?? [],
        });
      } catch (err) {
        console.error('[resolveAssetInputs] bundle fetch failed:', err);
        return { ...base, error: err instanceof Error ? err.message : String(err) };
      }
      // TAKEN AS GIVEN — no intersection here, deliberately.
      //
      // The endpoint was handed the selection and trimmed within it, so its
      // answer already IS "the top max_refs of what this card ticked", in the
      // provider-aware priority order (primary slot first, tail-dropped).
      // Re-filtering it against the same selection would be a no-op at best;
      // what this code used to do was the reverse — the endpoint trimmed over
      // ALL the asset's files and this line intersected afterwards, which is
      // `top_N(all) ∩ selection` and delivers NOTHING whenever the picks are
      // not a prefix of the priority order. Reordering by tick order would be
      // its own bug: the provider reads the first reference as the lead one.
      const ids = bundle.reference_resource_ids;
      return {
        ...base,
        urls: ids.map(assetReferenceUrl),
        positive: (bundle.prompt?.positive ?? '').trim(),
        negative: (bundle.prompt?.negative ?? '').trim(),
        dropped: Array.isArray(bundle.dropped) ? bundle.dropped : [],
      };
    }),
  );

  const urls: string[] = [];
  const seenUrl = new Set<string>();
  const positives: string[] = [];
  const negatives: string[] = [];
  const seenNegative = new Set<string>();
  for (const c of contributions) {
    for (const url of c.urls) {
      if (seenUrl.has(url)) continue;
      seenUrl.add(url);
      urls.push(url);
    }
    if (c.positive) positives.push(c.positive);
    if (c.negative && !seenNegative.has(c.negative)) {
      seenNegative.add(c.negative);
      negatives.push(c.negative);
    }
  }
  return {
    reference_urls: urls,
    prompt_prefix: positives.join('\n'),
    negative: negatives.join('\n'),
    contributions,
  };
}


/**
 * The generation model an asset card's references will be delivered to, or null
 * when that cannot be answered with one name.
 *
 * The card's checklist needs a provider ceiling (`caps.max_refs`) to grey rows
 * out, and the ceiling belongs to the MODEL — nine references for codex, none
 * for ark. The card itself has no model; the prompt it feeds does.
 *
 * ONE hop downstream, and only `prompt` nodes: a card wired to two prompts on
 * DIFFERENT models has no single ceiling, and greying by either one would
 * disable a file the other would have sent — a lie about the run in the
 * direction of hiding work. So the ambiguous case answers null, which
 * `useModelCapabilities` already defines as "unknown ⇒ render full support".
 * The backend's `dropped` list stays the authority either way.
 */
export function downstreamGenModel(
  nodeId: string,
  nodes: CanvasNode[],
  connections: CanvasConnection[],
): string | null {
  const byId = new Map(nodes.map((n) => [String(asObj(n).id), n]));
  const models = new Set<string>();
  for (const c of connections) {
    if (String(asObj(c).source) !== nodeId) continue;
    const target = byId.get(String(asObj(c).target));
    if (!target || String(asObj(target).type ?? '') !== 'prompt') continue;
    const model = (
      (asObj(target).data ?? {}) as { gen?: { model?: string } | null }
    ).gen?.model;
    if (model) models.add(model);
  }
  return models.size === 1 ? [...models][0] : null;
}
