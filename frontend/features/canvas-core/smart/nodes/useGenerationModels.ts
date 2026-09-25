// features/canvas-core/smart/nodes/useGenerationModels.ts
//
// Generation model catalog for the prompt node's model picker (G4-F1).
// One module-level cache shared by every picker, refreshed when stale or on
// window focus (see staleCatalog — the rows carry a probe status that changes
// without admin action). Failures degrade to an empty list (the picker shows
// only the catalog-default option) and are logged for the error funnel.

import {
  listGenerationModels,
  type GenerationModel,
} from '../../services/canvasGenerationService';
import { createStaleCatalog } from './staleCatalog';

const catalog = createStaleCatalog<GenerationModel>(
  () => listGenerationModels(),
  'useGenerationModels',
);

/** Test hook: reset the module cache between cases. */
export function _resetGenerationModelsCache(): void {
  catalog.reset();
}

export function useGenerationModels(kind?: 'image' | 'video'): GenerationModel[] {
  const models = catalog.useRows();
  return kind ? models.filter((m) => m.type === kind) : models;
}
