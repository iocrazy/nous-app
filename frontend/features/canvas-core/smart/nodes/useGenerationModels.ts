// features/canvas-core/smart/nodes/useGenerationModels.ts
//
// Image/video models for every generation picker (canvas composer, cover
// studio, asset sheet). Since spec 2026-09-25 §3.7 this is a mapping, not a
// request: the rows are the enabled platform image/video rows carried by the
// AI settings, with the runtime state from hooks/usePlatformStatus on top.
//
// The daemon rule the server used to apply to this list (`apply_readiness`)
// is applied here from the same status payload:
//   - a row that runs on the user's machine is hidden while the daemon cannot
//     run it (`local_ready === false`);
//   - a server twin is hidden while its local twin can run (`superseded`).
// Unknown (`local_ready === null`, status not fetched yet) is not a verdict —
// the row stays, and dispatch answers with a typed refusal if it must.
//
// TRANSITIONAL (P2 → P3): upscale-only image services (studio-upscale) need an
// input image and fail on every prompt, but the settings mapping cannot say
// so yet. Until P3 adds `AiPlatformModelEntry.generatable`, the list is
// intersected with the generation capability table's rows (same server
// predicate; the composer loads that table anyway). While the table is
// unknown the intersection is skipped rather than emptying the picker. The
// test "upscale-only rows never reach a generation picker" stays the gate
// when P3 swaps this for the field.

import { useMemo } from 'react';

import { usePlatformModels } from '../../../../hooks/usePlatformModels';
import { useGeneratableModelNames } from './useModelCapabilities';
import type { PlatformModelType } from '../../../../types/api';
import type { PlatformModelRow } from '../../../../utils/platformModel';

export type GenerationModel = PlatformModelRow;

const GENERATION_TYPES: readonly PlatformModelType[] = ['image', 'video'];
const IMAGE: readonly PlatformModelType[] = ['image'];
const VIDEO: readonly PlatformModelType[] = ['video'];

export function useGenerationModels(kind?: 'image' | 'video'): GenerationModel[] {
  const types = kind === 'image' ? IMAGE : kind === 'video' ? VIDEO : GENERATION_TYPES;
  const { rows, status } = usePlatformModels(types);
  const generatable = useGeneratableModelNames();
  return useMemo(
    () =>
      rows.filter(
        (m) =>
          status.localReady(m.name) !== false &&
          !status.superseded(m.name) &&
          (generatable === null || generatable.has(m.name)),
      ),
    [rows, status, generatable],
  );
}
