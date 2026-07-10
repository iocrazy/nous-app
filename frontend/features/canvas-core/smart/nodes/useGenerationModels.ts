// features/canvas-core/smart/nodes/useGenerationModels.ts
//
// Generation model catalog for the prompt node's model picker (G4-F1).
// Fetched once per session (module cache) — the catalog changes via admin
// action, not mid-edit. Failures degrade to an empty list (the picker shows
// only the catalog-default option) and are logged for the error funnel.

import { useEffect, useState } from 'react';

import {
  listGenerationModels,
  type GenerationModel,
} from '../../services/canvasGenerationService';

let cache: GenerationModel[] | null = null;
let inflight: Promise<GenerationModel[]> | null = null;

/** Test hook: reset the module cache between cases. */
export function _resetGenerationModelsCache(): void {
  cache = null;
  inflight = null;
}

export function useGenerationModels(kind?: 'image' | 'video'): GenerationModel[] {
  const [models, setModels] = useState<GenerationModel[]>(cache ?? []);

  useEffect(() => {
    if (cache) return undefined;
    let live = true;
    inflight = inflight ?? listGenerationModels();
    inflight
      .then((m) => {
        cache = m;
        if (live) setModels(m);
      })
      .catch((err: unknown) => {
        console.error('[useGenerationModels] catalog fetch failed:', err);
      });
    return () => {
      live = false;
    };
  }, []);

  return kind ? models.filter((m) => m.type === kind) : models;
}
