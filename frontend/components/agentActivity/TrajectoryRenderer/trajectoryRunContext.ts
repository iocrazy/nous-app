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

/**
 * The issue whose thread is rendering this trajectory — `MH-96`.
 *
 * Read by the citations line, which names the SOURCE issue of every version
 * the turn pointed at. The backend fills that source in for **every**
 * resolvable citation (`output_ref_resolver._stamped` compares nothing), so
 * "does it have one" is not the question — "is it a different one" is. Without
 * an answer to that, every citation carries a label pointing at the issue the
 * reader is already on, and the one that genuinely came from elsewhere stops
 * standing out.
 *
 * `null` = the surface did not say which issue it is (the chat Trajectory tab
 * has no issue behind it at all, the Task Center detail does not track one).
 * Then nothing is compared and every source is drawn — which is the honest
 * answer: we cannot claim a citation is "from here".
 *
 * A context for the same reason the run id is one: `DetachedRunPanel` renders
 * its own trajectory, and the node that reads this sits several components
 * below whoever knows the issue.
 */
export const TrajectoryIssueKeyContext = createContext<string | null>(null);

export function useTrajectoryIssueKey(): string | null {
  return useContext(TrajectoryIssueKeyContext);
}
