// features/canvas-core/smart/nodes/useModelCapabilities.ts
//
// Per-model generation capabilities for the composer's knobs (P4). Fetched
// once per session (module cache) like useGenerationModels — the projection
// changes with the catalog, not mid-edit.
//
// The return value is `ModelCapabilities | null`, and `null` is a deliberate
// value rather than an error state: it means "capabilities unknown" (still
// loading, fetch failed, old backend without the endpoint, model absent from
// the map). Every consumer renders FULL support on `null`, so a failure must
// settle as null-yielding and NEVER as an empty map — an empty map would read
// as "this model supports nothing" and hide every knob on the day the backend
// hiccups.

import { useEffect, useMemo, useState } from 'react';

import {
  listGenerationCapabilities,
  type ModelCapabilities,
} from '../../services/canvasGenerationService';

/** The resolved map, or null once a fetch has failed (see `settled`). */
let cache: Record<string, ModelCapabilities> | null = null;
/** True once one fetch has settled either way — a failure settles with
 *  `cache = null`, which every lookup reads as "unknown". */
let settled = false;
let inflight: Promise<Record<string, ModelCapabilities>> | null = null;

/** Test hook: reset the module cache between cases. */
export function _resetModelCapabilitiesCache(): void {
  cache = null;
  settled = false;
  inflight = null;
}

/** null = capabilities unknown (loading / fetch failed / old backend / model
 *  not in the map) → callers must render FULL support. */
export function useModelCapabilities(model: string | null | undefined): ModelCapabilities | null {
  const [caps, setCaps] = useState<Record<string, ModelCapabilities> | null>(cache);

  useEffect(() => {
    // Re-sync from the module cache first: an instance that mounted with no
    // model name never subscribed to the fetch, so when its name arrives the
    // cache may already have settled elsewhere. Passing the same reference
    // lets React bail out, so this cannot loop.
    if (settled) {
      setCaps(cache);
      return undefined;
    }
    if (!model) return undefined;
    let live = true;
    inflight = inflight ?? listGenerationCapabilities();
    inflight
      .then((m) => {
        cache = m;
        settled = true;
        if (live) setCaps(m);
      })
      .catch((err: unknown) => {
        cache = null;
        settled = true;
        console.error('[useModelCapabilities] capabilities fetch failed:', err);
      });
    return () => {
      live = false;
    };
  }, [model]);

  if (!model) return null;
  return caps?.[model] ?? null;
}

/**
 * TRANSITIONAL (spec 2026-09-25 P2 → P3): names of the rows the generation
 * capability table lists, or null while unknown (loading / failed).
 *
 * The platform list on the AI settings does not yet say whether a row can
 * generate from a prompt; the server's generation-row predicate drops
 * upscale-only image services (`text_to_image=False`, e.g. studio-upscale),
 * and this table is keyed by exactly that predicate. useGenerationModels
 * intersects with it until P3 adds `AiPlatformModelEntry.generatable` —
 * then this hook and the intersection go.
 *
 * Shares the module cache and the one in-flight request with
 * useModelCapabilities: at most one `generation-capabilities` call per page.
 */
export function useGeneratableModelNames(): ReadonlySet<string> | null {
  const [caps, setCaps] = useState<Record<string, ModelCapabilities> | null>(cache);

  useEffect(() => {
    if (settled) {
      setCaps(cache);
      return undefined;
    }
    let live = true;
    inflight = inflight ?? listGenerationCapabilities();
    inflight
      .then((m) => {
        cache = m;
        settled = true;
        if (live) setCaps(m);
      })
      .catch((err: unknown) => {
        cache = null;
        settled = true;
        console.error('[useGeneratableModelNames] capabilities fetch failed:', err);
      });
    return () => {
      live = false;
    };
  }, []);

  return useMemo(() => (caps ? new Set(Object.keys(caps)) : null), [caps]);
}
