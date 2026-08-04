/**
 * Batch module-switch status (Module Control Center, admin-controlled).
 * One request per page lifetime (60s cache + in-flight dedupe) feeds every
 * useModuleStatus() consumer. Returns null on failure — callers apply
 * per-module fail defaults, this layer never guesses.
 */
import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';

export interface ModuleStatus {
  enabled: boolean;
  visible: boolean;
}

const TTL_MS = 60_000;

let cache: { at: number; data: Record<string, ModuleStatus> } | null = null;
let inFlight: Promise<Record<string, ModuleStatus> | null> | null = null;

export async function fetchModulesStatus(): Promise<Record<string, ModuleStatus> | null> {
  if (cache && Date.now() - cache.at < TTL_MS) return cache.data;
  if (inFlight) return inFlight;
  inFlight = (async () => {
    try {
      const resp = await fetch(`${getApiUrl()}/api/v1/modules/status`, {
        headers: await getAuthHeaders(),
      });
      if (!resp.ok) throw new Error(`modules/status ${resp.status}`);
      const body = await resp.json();
      const map: Record<string, ModuleStatus> = {};
      for (const m of body.modules ?? []) {
        map[m.id] = { enabled: m.enabled !== false, visible: m.visible !== false };
      }
      cache = { at: Date.now(), data: map };
      return map;
    } catch (err) {
      console.error('modules/status load failed', err);
      return null;
    } finally {
      inFlight = null;
    }
  })();
  return inFlight;
}

/** Test seam: reset the module-level cache between test cases. */
export function __resetModulesStatusCache(): void {
  cache = null;
  inFlight = null;
}
