// frontend/hooks/usePlatformModels.ts
//
// The platform rows a picker offers, with live status overlaid. Pure mapping
// over the AI settings (utils/platformModel › platformModelRows) plus the one
// status request (hooks/usePlatformStatus) — no catalog request of its own, so
// every surface that uses it shows the same list (spec 2026-09-25 §3.7).

import { useMemo } from 'react';

import { useOptionalAISettings } from '../contexts/AuthContext';
import type { AISettings } from '../types';
import type { PlatformModelType } from '../types/api';
import { platformModelRows, type PlatformModelRow } from '../utils/platformModel';
import { usePlatformStatus, type PlatformStatusView } from './usePlatformStatus';

/** Rows the user has enabled, of these types, with `status` live. */
export function usePlatformModels(
  types: readonly PlatformModelType[],
  settings?: AISettings | null,
): { rows: PlatformModelRow[]; status: PlatformStatusView } {
  const loaded = useOptionalAISettings();
  const source = settings === undefined ? loaded : settings;
  const status = usePlatformStatus(source);
  const typeKey = types.join(',');
  const rows = useMemo(
    () =>
      platformModelRows(source, { types: typeKey.split(',') as PlatformModelType[] }).map(
        (row) => ({ ...row, status: status.statusOf(row.name) ?? row.status }),
      ),
    [source, typeKey, status],
  );
  return { rows, status };
}

const TEXT_TYPES: readonly PlatformModelType[] = ['llm'];

/** Enabled platform `llm` rows — the text-prompt pickers on the canvas. */
export function useTextPlatformModels(): PlatformModelRow[] {
  return usePlatformModels(TEXT_TYPES).rows;
}
