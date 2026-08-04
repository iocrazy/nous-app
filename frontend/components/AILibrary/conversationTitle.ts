// frontend/components/AILibrary/conversationTitle.ts
// What the workbench's recent-conversations row calls a run group.
//
// `latest_output_summary` is the tail of whatever the agent produced, so it is
// prose only by luck. A storyboard run ends with a JSON payload and the row
// rendered `{"shots": [{"title": ...` verbatim — unreadable, and long enough
// to blow the grid column sideways. The old fallback was no better: `trigger`
// printed a bare `script_ai`, naming the agent you are already looking at.
//
// Kept as a pure function so the rules are testable without a DOM.

import type { AgentRunGroupItem } from '../../types';

/** Past this the row is truncated visually anyway; cap so one pathological
 *  summary cannot dominate layout cost or the accessible name. */
const MAX_LEN = 120;

export function conversationTitle(
  group: Pick<AgentRunGroupItem, 'latest_output_summary'>,
  fallback: string,
): string {
  const raw = group.latest_output_summary ?? '';

  // Leading whitespace/newlines must not hide the JSON marker.
  const trimmed = raw.trim();
  if (!trimmed) return fallback;
  if (trimmed.startsWith('{') || trimmed.startsWith('[')) return fallback;

  // A summary can be a whole paragraph; the row is one line.
  const firstLine = trimmed.split('\n')[0].replace(/\s+/g, ' ').trim();
  if (!firstLine) return fallback;

  return firstLine.length > MAX_LEN ? `${firstLine.slice(0, MAX_LEN - 1)}…` : firstLine;
}
