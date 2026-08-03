/**
 * Merge a Realtime `issues` UPDATE payload onto the row already in the list.
 *
 * The `issues` publication carries an explicit column list (migration 172)
 * that EXCLUDES `execution_state`, `execution_locked_at` and
 * `dbos_workflow_id`. Replacing the list row wholesale therefore blanks those
 * three every time any unrelated column changes — and since the running chip
 * keys off `dbos_workflow_id`, a live agent's row visibly flickers off until
 * the next REST fetch puts it back.
 *
 * So: whitelisted columns come from the incoming payload (that is the whole
 * point of subscribing), everything the publication cannot deliver is carried
 * over from the row we already have. A Realtime event is a "refetch me" signal
 * for those fields, never a source of truth for them.
 */

import type { Issue } from '../../services/issuesService';
import type { AgentRef, UiIssue } from './types';
import { toUiIssue, type ProjectNameMap } from './uiIssue';

/**
 * Columns absent from the `issues` Realtime publication (migration 172).
 * `goal_id` / `origin_fingerprint` are excluded there too but the UI never
 * reads them, so they are left out of the carry-over set deliberately.
 */
const PUBLICATION_EXCLUDED = [
  'dbos_workflow_id',
  'execution_state',
  'execution_locked_at',
] as const;

export function mergeRealtimeIssue(
  prev: UiIssue,
  incoming: Issue,
  agentsById: Record<string, AgentRef>,
  projectsById?: ProjectNameMap,
): UiIssue {
  const carried: Record<string, unknown> = {};
  const prevRaw = (prev?.raw ?? {}) as unknown as Record<string, unknown>;
  for (const key of PUBLICATION_EXCLUDED) {
    // Only fill a gap. If the publication is ever widened, fresh data wins.
    if ((incoming as unknown as Record<string, unknown>)[key] === undefined) {
      carried[key] = prevRaw[key];
    }
  }
  return toUiIssue({ ...incoming, ...carried } as Issue, agentsById, projectsById);
}
