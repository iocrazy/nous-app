// frontend/hooks/usePlatformStatus.ts
//
// The ONE request point for platform-model runtime state (spec 2026-09-25
// §3.3). The platform LIST rides on the AI settings (`providers.nous` +
// `platform_models`, loaded once after login); what changes fast — nous-engine
// ok/idle, engine reachability, whether the user's own daemon can run a local
// row — comes from `GET /ai/platform-status` through this hook, and every
// picker overlays it on the list the same way.
//
// Cache policy is utils/staleResource: one module-level value for the whole
// page, refetched on a mount after 10 minutes and on window focus (at most
// once per 30 seconds), and a failed refresh keeps the last good value. Before
// the first answer lands — or when it never does — `statusOf` falls back to
// the status the settings carried, so a picker is never blank for want of it.

import { useCallback, useMemo } from 'react';

import { useOptionalAISettings } from '../contexts/AuthContext';
import { getPlatformStatus } from '../services/aiService';
import type { AISettings } from '../types';
import type {
  PlatformEngineState,
  PlatformModelStatus,
  PlatformStatusResponse,
} from '../types/api';
import { createStaleResource } from '../utils/staleResource';

const resource = createStaleResource<PlatformStatusResponse>(
  () => getPlatformStatus(),
  'usePlatformStatus',
);

/** Test hook: forget the cached status between cases. */
export function _resetPlatformStatusCache(): void {
  resource.reset();
}

export interface PlatformStatusView {
  /** Live status if known, else the one the settings carried, else undefined. */
  statusOf: (name: string) => PlatformModelStatus | undefined;
  /** `false` = runs on the user's machine and the daemon cannot run it now;
   *  `true` = it can; `null` = not a local row, or not known yet. */
  localReady: (name: string) => boolean | null;
  /** A server twin hidden because its local twin can run right now. */
  superseded: (name: string) => boolean;
  /** Live engine reachability if known, else the settings' value. */
  engine: PlatformEngineState | null;
}

/**
 * Runtime state for the platform rows. `settings` defaults to the ones loaded
 * after login (AuthContext); the Settings page passes its own copy.
 */
export function usePlatformStatus(settings?: AISettings | null): PlatformStatusView {
  const loaded = useOptionalAISettings();
  const source = settings === undefined ? loaded : settings;
  const live = resource.useValue();
  const mapping = source?.platform_models ?? null;

  const statusOf = useCallback(
    (name: string): PlatformModelStatus | undefined =>
      live?.models[name]?.status ?? mapping?.[name]?.status,
    [live, mapping],
  );
  const localReady = useCallback(
    (name: string): boolean | null => live?.models[name]?.local_ready ?? null,
    [live],
  );
  const superseded = useCallback(
    (name: string): boolean => live?.models[name]?.superseded ?? false,
    [live],
  );
  const engine = live ? live.engine : (source?.platform_engine ?? null);

  return useMemo(
    () => ({ statusOf, localReady, superseded, engine }),
    [statusOf, localReady, superseded, engine],
  );
}
