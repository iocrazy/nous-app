// features/canvas-core/smart/nodes/useTextModels.ts
//
// Text (llm) model catalog for the prompt node's provider picker (P0-1).
// Mirrors useGenerationModels: one module-level cache shared by every picker,
// refreshed when stale or on window focus (see staleCatalog — the rows carry
// a probe status that changes without admin action). Failures degrade to an
// empty list (the picker shows only the catalog-default option) and are logged
// for the error funnel.
//
// This is the fix for the 2026-07-12 data-source drift: the text prompt's
// model list now comes from the SAME platform DB catalog the image/video
// picker uses, instead of a hardcoded constant that named `qwen-plus` /
// `nous/storyboard` — models the platform doesn't actually carry.

import { listTextModels, type TextModel } from '../../services/canvasGenerationService';
import { createStaleCatalog } from './staleCatalog';

const catalog = createStaleCatalog<TextModel>(() => listTextModels(), 'useTextModels');

/** Test hook: reset the module cache between cases. */
export function _resetTextModelsCache(): void {
  catalog.reset();
}

export function useTextModels(): TextModel[] {
  return catalog.useRows();
}
