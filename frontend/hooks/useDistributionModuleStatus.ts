import { useEffect, useState } from 'react';
import {
  getModuleStatus,
  type DistributionModuleStatus,
} from '../services/distributionService';

interface DistributionModuleState extends DistributionModuleStatus {
  /** True until the first status read resolves — a route guard must wait for
   * this before deciding to redirect, otherwise the fail-closed default would
   * bounce the user out before the real (possibly enabled) status arrives. */
  loading: boolean;
}

/**
 * Distribution module switches (admin-controlled via
 * `system_settings['distribution.module']`), mirroring `useTopicModuleStatus`.
 *
 * - `visible` — display switch: gates the nav entry + routes.
 * - `enabled` — access switch: whether the backend account/OAuth API is on.
 *
 * Unlike Topic Inspiration (which defaults ON), this OPT-IN module defaults
 * OFF and fails CLOSED: while loading / on error both stay false so a
 * not-yet-launched, token-handling surface never flashes into view.
 */
export function useDistributionModuleStatus(): DistributionModuleState {
  const [state, setState] = useState<DistributionModuleState>({
    enabled: false,
    visible: false,
    loading: true,
  });
  useEffect(() => {
    let alive = true;
    getModuleStatus().then((v) => {
      if (alive) setState({ ...v, loading: false });
    });
    return () => {
      alive = false;
    };
  }, []);
  return state;
}
