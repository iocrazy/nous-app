/**
 * Which run's transcript the surrounding trajectory is folding.
 *
 * A node renderer sits several components below whoever fetched the events,
 * and one of them — the sub-agent card — has to name that run: the sub-run
 * panel's header says where the child came from, and the card is the only
 * place that knows. It hardcoded `null` until Task 7b defect H, so the header
 * always read «from run #—» in production.
 *
 * A context rather than a prop on every node: `DetachedRunPanel` renders its
 * OWN `RunTrajectory` inside the page, so the answer is per-trajectory and not
 * per-page — a value threaded from the page would be wrong in exactly the
 * place this is read.
 *
 * `null` = the surface rendering this trajectory did not say (the chat
 * Trajectory tab, the Task Center detail). A card then passes `null` on, and
 * the header falls back to the dash as before.
 */
import { createContext, useContext } from 'react';

export const TrajectoryRunContext = createContext<string | null>(null);

export function useTrajectoryRunId(): string | null {
  return useContext(TrajectoryRunContext);
}
