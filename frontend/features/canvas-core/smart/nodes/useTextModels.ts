// features/canvas-core/smart/nodes/useTextModels.ts
//
// Text (llm) model catalog for the prompt node's provider picker (P0-1).
// Mirrors useGenerationModels: fetched once per session (module cache) since
// the catalog changes via admin action, not mid-edit. Failures degrade to an
// empty list (the picker shows only the catalog-default option) and are logged
// for the error funnel.
//
// This is the fix for the 2026-07-12 data-source drift: the text prompt's
// model list now comes from the SAME platform DB catalog the image/video
// picker uses, instead of a hardcoded constant that named `qwen-plus` /
// `nous/storyboard` — models the platform doesn't actually carry.

import { useEffect, useState } from 'react';

import { listTextModels, type TextModel } from '../../services/canvasGenerationService';

let cache: TextModel[] | null = null;
let inflight: Promise<TextModel[]> | null = null;

/** Test hook: reset the module cache between cases. */
export function _resetTextModelsCache(): void {
  cache = null;
  inflight = null;
}

export function useTextModels(): TextModel[] {
  const [models, setModels] = useState<TextModel[]>(cache ?? []);

  useEffect(() => {
    if (cache) return undefined;
    let live = true;
    inflight = inflight ?? listTextModels();
    inflight
      .then((m) => {
        cache = m;
        if (live) setModels(m);
      })
      .catch((err: unknown) => {
        console.error('[useTextModels] catalog fetch failed:', err);
      });
    return () => {
      live = false;
    };
  }, []);

  return models;
}
