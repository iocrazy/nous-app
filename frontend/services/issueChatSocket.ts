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
  // `run_id` / `seq` / `outputs` ride the `done` frame (3b Task 4b): always
  // present on that backend, null / `[]` when unknown. There is no separate
  // `deliverable` frame — this socket relays chunk / message / status only, and
  // what a run registered arrives here.
  //
  // All three are OPTIONAL because an older backend omits them entirely: a
  // missing `seq` reads as 0 and missing `outputs` as `[]`. That is safe
  // because the signal's watermark is per `(issue, run)` — a frame with no
  // `run_id` lands in the local lane and cannot mask a real run's frames.
  // `cost_cents` / `charged_points` (3c §4.2) ride EVERY status frame, not just
  // `done` — one shape to read. Both are nullable and the two nulls say
  // different things a 0 would lie about: no recorded cost, and **nobody
  // billed this run** (BYOK, billing off, or the charge never landed).
  // Optional here for the same reason as the three above: an older backend
  // omits them, and «absent» must not be read as «free».
  | {
      type: 'status';
      phase: 'running' | 'done' | string;
      run_id?: string | null;
      seq?: number | null;
      outputs?: Array<{ kind: string; ref_id: string }>;
      cost_cents?: number | null;
      charged_points?: number | null;
    };

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
