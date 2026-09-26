// features/canvas-core/smart/nodes/useGenerationModels.ts
//
// Image/video models for every generation picker (canvas composer, cover
// studio, asset sheet). Since spec 2026-09-25 §3.7 this is a mapping, not a
// request: the rows are the enabled platform image/video rows carried by the
// AI settings, with the runtime state from hooks/usePlatformStatus on top.
//
// The daemon rule is computed server-side per row
// (`services/generation/local_readiness.local_verdict`, served by
// `GET /ai/platform-status`) and applied here from that payload:
//   - a row that runs on the user's machine is hidden while the daemon cannot
//     run it (`local_ready === false`);
//   - a server twin is hidden while its local twin can run (`superseded`).
// Unknown (`local_ready === null`, status not fetched yet) is not a verdict —
// the row stays, and dispatch answers with a typed refusal if it must.
//
// Only rows the server marks `generatable` are offered: upscale-only services
// (nous-engine super-resolution) need an input image and would fail on every
// prompt. `generatable` is the same predicate the server's generation row set
// uses (backend services/generation/model_capabilities.generates_from_prompt).

import { useMemo } from 'react';

import { usePlatformModels } from '../../../../hooks/usePlatformModels';
import type { PlatformModelType } from '../../../../types/api';
import type { PlatformModelRow } from '../../../../utils/platformModel';

export type GenerationModel = PlatformModelRow;

const GENERATION_TYPES: readonly PlatformModelType[] = ['image', 'video'];
const IMAGE: readonly PlatformModelType[] = ['image'];
const VIDEO: readonly PlatformModelType[] = ['video'];

export function useGenerationModels(kind?: 'image' | 'video'): GenerationModel[] {
  const types = kind === 'image' ? IMAGE : kind === 'video' ? VIDEO : GENERATION_TYPES;
  const { rows, status } = usePlatformModels(types);
  return useMemo(
    () =>
      rows.filter(
        (m) =>
          m.generatable &&
          status.localReady(m.name) !== false &&
          !status.superseded(m.name),
      ),
    [rows, status],
  );
}
