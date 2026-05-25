/**
 * Issue chat WebSocket client.
 *
 * Opens `/ws/issue/{issueId}` authenticated via a one-shot ticket
 * (same pattern as `/ws/task-progress` in TaskManagerContext).
 * Parses incoming JSON frames and dispatches them to the caller
 * via the `onEvent` callback.
 */

import { wsTicketService } from './wsTicketService';
import { getApiUrl } from '../utils/apiConfig';
import type { IssueMessage } from './issueMessageService';

// Mirror how TaskManagerContext builds the WS URL:
//   const base = API_BASE || window.location.origin;
//   return base.replace(/^http/, 'ws');
function getWsBaseUrl(): string {
  const base = getApiUrl() || window.location.origin;
  return base.replace(/^http/, 'ws');
}

export type IssueChatEvent =
  | { type: 'chunk'; delta: string }
  | { type: 'message'; message: IssueMessage }
  | { type: 'status'; phase: 'running' | 'done' | string };

/**
 * Mint a one-shot ticket, open a WebSocket to `/ws/issue/{issueId}`,
 * and wire `onEvent` to all incoming JSON frames.
 *
 * Returns the WebSocket so the caller can close it on unmount.
 * Throws if ticket acquisition fails.
 */
export async function openIssueChatSocket(
  issueId: number,
  onEvent: (e: IssueChatEvent) => void,
): Promise<WebSocket> {
  const { ticket } = await wsTicketService.acquire();
  const ws = new WebSocket(
    `${getWsBaseUrl()}/ws/issue/${issueId}?ticket=${encodeURIComponent(ticket)}`,
  );
  ws.onmessage = (ev) => {
    try {
      onEvent(JSON.parse(ev.data) as IssueChatEvent);
    } catch {
      // Ignore malformed frames — don't crash the session.
    }
  };
  return ws;
}
