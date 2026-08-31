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

import { useEffect, useState } from 'react';

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
    if (!model || settled) return undefined;
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
