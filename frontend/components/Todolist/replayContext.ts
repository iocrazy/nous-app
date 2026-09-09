/**
 * Replay state (harness 2b-1 §1) shared by the detail page (owner), the run
 * trajectory (scrubber + event slice) and the cockpit ("as of step N").
 * `seq === null` is Live. `runId` is the run the scrubber attaches to — the
 * newest run of the issue.
 */
import { createContext, useContext } from 'react';
import type { RunCost, RunView } from '../TaskCenter/runView';

export interface ReplayState {
  runId: string | null;
  seq: number | null;
  view: RunView | null;
  cost: RunCost | null;
  loading: boolean;
  seek: (seq: number | null) => void;
  /** Phase 2b-1 §2 (Task 6): fork from this seq; absent when not an issue run. */
  fork?: (seq: number) => void;
}

export const ReplayContext = createContext<ReplayState | null>(null);

export function useReplay(): ReplayState | null {
  return useContext(ReplayContext);
}

/** True when `runId` is being viewed as of a past step (not Live). */
export function isReplaying(replay: ReplayState | null, runId: string | null | undefined): boolean {
  return !!replay && !!runId && replay.runId === runId && replay.seq != null;
}
