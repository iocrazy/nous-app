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

/** A run is over once the issue lands here; the workflow id lingers either way. */
const TERMINAL_STATUSES = new Set(['done', 'cancelled']);

/**
 * Should this Realtime event trigger a single-row REST refetch?
 *
 * The carry-over above keeps a live row from flickering, but it cannot make it
 * ADVANCE: `execution_state` is outside the mig-172 publication, so the turn
 * counter and the elapsed clock stay frozen at whatever the last REST fetch
 * saw. For a row with a run in flight the event is therefore a "something
 * moved, come and look" signal — exactly the posture TaskManagerContext takes.
 *
 * Deliberately narrow, because every `true` costs a request: only rows that
 * were dispatched (`dbos_workflow_id`) and have not finished. Status is read
 * from the INCOMING payload when it carries one — it is whitelisted, so the
 * event that ends a run already knows the run ended, and refetching then would
 * buy nothing.
 */
export function shouldRefetchOnRealtime(
  prev: UiIssue | undefined,
  incoming: Partial<Issue>,
): boolean {
  // No prior row means an INSERT: the payload is all there is, and it is whole.
  if (!prev?.raw?.dbos_workflow_id) return false;
  const status = incoming.status ?? prev.status;
  return !TERMINAL_STATUSES.has(status);
}
