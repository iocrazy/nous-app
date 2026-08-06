// frontend/components/AILibrary/conversationTitle.ts
// What the workbench's recent-conversations row calls a run group.
//
// The name comes from the server (`title`): conversation title → issue title →
// first user message. That chain exists because the only source this file
// used to have — `latest_output_summary` — is the tail of whatever the agent
// produced, so it is prose only by luck. Measured on prod: 44 of 99 runs carry
// no summary and 20 more end in a JSON payload, which is exactly how every row
// came to read "Untitled conversation". See list_groups_by_agent's docstring.
//
// The summary is kept as a second choice rather than dropped: a group the
// server cannot name (a storyboard or captioning run — no conversation, no
// issue) may still have said something readable, and half a sentence beats a
// neutral label. Everything that is machine output gets filtered out below.
//
// Kept as a pure function so the rules are testable without a DOM.

import type { AgentRunGroupItem } from '../../types';

/** Past this the row is truncated visually anyway; cap so one pathological
 *  summary cannot dominate layout cost or the accessible name. */
const MAX_LEN = 120;

/**
 * Openers that mean "this is machine output, not a sentence".
 *
 * A bare `{` / `[` is the JSON dump this filter was written for. The fence is
 * the same payload wearing a markdown coat — prod has plenty of ` ```json `
 * followed by the object, and matching only the bare brace let those through
 * as the literal title "```json". `<` covers the XML-shaped variants.
 */
const PAYLOAD_OPENERS = ['{', '[', '```', '<'];

/** One line, whitespace collapsed, capped. Empty when nothing usable survives. */
function summaryAsTitle(raw: string): string {
  // Leading whitespace/newlines must not hide the opener.
  const trimmed = raw.trim();
  if (!trimmed || PAYLOAD_OPENERS.some((o) => trimmed.startsWith(o))) return '';

  // A summary can be a whole paragraph; the row is one line.
  const firstLine = trimmed.split('\n')[0].replace(/\s+/g, ' ').trim();
  if (!firstLine) return '';

  return firstLine.length > MAX_LEN ? `${firstLine.slice(0, MAX_LEN - 1)}…` : firstLine;
}

export function conversationTitle(
  group: Pick<AgentRunGroupItem, 'latest_output_summary' | 'title'>,
  fallback: string,
): string {
  // The server-side name is already one line and already capped. It is a
  // title, not output, so it is deliberately NOT payload-filtered — a chat
  // someone called "{draft} pass one" keeps that name.
  const named = (group.title ?? '').trim();
  if (named) return named;

  return summaryAsTitle(group.latest_output_summary ?? '') || fallback;
}
