/**
 * Lane queue admin API client (A 路线 PR #161).
 *
 * Process-local snapshot — each backend replica returns its own state.
 * For cluster-wide saturation, query each replica.
 */

import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';

const base = (): string => `${getApiUrl()}/api/v1/lanes`;

export interface LaneStatus {
  name: string;
  capacity: number;
  in_flight: number;
  queued: number;
  total_acquired: number;
  last_wait_ms: number;
  saturation_pct: number;
}

export interface LanesSnapshotResponse {
  lanes: LaneStatus[];
}

export const lanesService = {
  snapshot: async (): Promise<LanesSnapshotResponse> => {
    const headers = await getAuthHeaders();
    const res = await fetch(`${base()}/snapshot`, { headers });
    if (!res.ok) {
      const txt = await res.text().catch(() => '');
      throw new Error(`Lanes API ${res.status}: ${txt || res.statusText}`);
    }
    return res.json();
  },
};
