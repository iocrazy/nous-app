/**
 * Pure render-layer grouping for the unified issue Timeline.
 *
 * Long-lived issues can accumulate many consecutive `system_status`
 * events (a task bounced todo → in_progress → todo → … many times),
 * which floods the timeline and buries the actual conversation. This
 * helper coalesces a RUN of >= COALESCE_THRESHOLD consecutive
 * `system_status` messages into a single collapsible group row, while
 * leaving shorter runs (1–2) inline so the thread isn't peppered with
 * expand toggles.
 *
 * This is a pure, side-effect-free transform: it reads `IssueMessage[]`
 * and returns a new render-item list WITHOUT mutating the input array or
 * any of its elements. New messages arriving via Realtime simply re-run
 * this function, so grouping always reflects the latest data.
 */

import type { IssueMessage } from '../../services/issueMessageService';

/**
 * Minimum length of a consecutive `system_status` run before it collapses
 * into a group. Runs of 1–2 stay inline — collapsing those would add more
 * expand toggles than it removes noise.
 */
export const COALESCE_THRESHOLD = 3;

/** A single message rendered inline (comment / agent_run / lone status). */
export interface TimelineMessageItem {
  type: 'message';
  /** Stable key for React — the underlying message id. */
  key: string;
  message: IssueMessage;
}

/** A collapsed run of consecutive `system_status` messages. */
export interface TimelineStatusGroupItem {
  type: 'status_group';
  /** Stable key for React — derived from the first & last member ids. */
  key: string;
  messages: IssueMessage[];
}

export type TimelineRenderItem = TimelineMessageItem | TimelineStatusGroupItem;

/**
 * Group consecutive `system_status` runs of length >= COALESCE_THRESHOLD
 * into `status_group` items; everything else (including short status runs)
 * becomes a plain `message` item, preserving original order.
 *
 * Returns a brand-new array; never mutates `messages` or its elements.
 */
export function coalesceSystemStatus(messages: IssueMessage[]): TimelineRenderItem[] {
  const items: TimelineRenderItem[] = [];
  const total = messages.length;
  let i = 0;

  while (i < total) {
    const msg = messages[i];

    if (msg.kind === 'system_status') {
      // Measure the length of this consecutive system_status run.
      let runEnd = i;
      while (runEnd < total && messages[runEnd].kind === 'system_status') {
        runEnd += 1;
      }
      const runLength = runEnd - i;

      if (runLength >= COALESCE_THRESHOLD) {
        const groupMessages = messages.slice(i, runEnd);
        const first = groupMessages[0];
        const last = groupMessages[groupMessages.length - 1];
        items.push({
          type: 'status_group',
          key: `status-group-${first.id}-${last.id}`,
          messages: groupMessages,
        });
      } else {
        // Short run — keep each status event inline.
        for (let j = i; j < runEnd; j += 1) {
          items.push({ type: 'message', key: messages[j].id, message: messages[j] });
        }
      }
      i = runEnd;
      continue;
    }

    items.push({ type: 'message', key: msg.id, message: msg });
    i += 1;
  }

  return items;
}
