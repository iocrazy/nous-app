import { useEffect, useState } from 'react';
import { fetchModulesStatus, type ModuleStatus } from '../services/modulesService';

/**
 * One hook for every Module Control Center switch consumer.
 * Fail defaults are per-module: launched modules fail OPEN (a transient
 * error must never hide a shipped feature); opt-in distribution fails
 * CLOSED (unlaunched surface never flashes into view).
 */
const FAIL_DEFAULTS: Record<string, ModuleStatus> = {
  distribution: { enabled: false, visible: false },
};
const OPEN: ModuleStatus = { enabled: true, visible: true };

export interface ModuleStatusState extends ModuleStatus {
  /** True until the first read resolves — fail-closed guards must wait for
   * this before hiding a surface, or distribution would flash its disabled
   * page on every load. */
  loading: boolean;
}

export function useModuleStatus(id: string): ModuleStatusState {
  const fallback = FAIL_DEFAULTS[id] ?? OPEN;
  const [state, setState] = useState<ModuleStatusState>({ ...fallback, loading: true });
  useEffect(() => {
    let alive = true;
    fetchModulesStatus().then((map) => {
      if (!alive) return;
      setState({ ...(map?.[id] ?? fallback), loading: false });
    });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);
  return state;
}
