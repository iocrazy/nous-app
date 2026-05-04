/**
 * Short-lived WebSocket ticket client (A 路线 PR #158).
 *
 * Replaces `?token=<JWT>` URL auth with `?ticket=<random>` to keep JWTs
 * out of access logs / browser history / Sentry traces. Backend stores
 * the ticket in Redis with 30s TTL, one-shot consume.
 */

import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';

export interface TicketResponse {
  ticket: string;
  expires_in_seconds: number;
}

export const wsTicketService = {
  /**
   * Acquire a one-shot ticket. Throws if auth fails.
   *
   * Caller pattern:
   *   const { ticket } = await wsTicketService.acquire();
   *   const ws = new WebSocket(`${WS_URL}/ws/task-progress?ticket=${ticket}`);
   *
   * On WS close, do not reuse the ticket — acquire a new one.
   */
  acquire: async (): Promise<TicketResponse> => {
    const headers = await getAuthHeaders();
    const res = await fetch(`${getApiUrl()}/api/v1/ws/ticket`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...headers },
    });
    if (!res.ok) {
      const txt = await res.text().catch(() => '');
      throw new Error(`WS ticket ${res.status}: ${txt || res.statusText}`);
    }
    return res.json();
  },
};
