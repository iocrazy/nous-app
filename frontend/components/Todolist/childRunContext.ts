/**
 * Which sub-run the issue page is showing beside the thread (harness 2b-2
 * §5-1). A sub-agent card lives deep inside the trajectory renderer while the
 * panel that shows the child run belongs to the page, so the two talk through
 * this context rather than through a prop chain across three components.
 *
 * `null` = no sub-run open. The origin travels with the id because the panel
 * header states where the child came from, and that is knowledge the card
 * has and the panel does not.
 */
import { createContext, useContext } from 'react';

export interface ChildRunOrigin {
  childRunId: string;
  /** The run that spawned it; null when the card cannot name it. */
  parentRunId: string | null;
  step: number;
  mode: 'sync' | 'async';
  subagentType: string;
  description: string;
}

export interface ChildRunState {
  current: ChildRunOrigin | null;
  open: (origin: ChildRunOrigin) => void;
  close: () => void;
}

export const ChildRunContext = createContext<ChildRunState | null>(null);

/** Null outside a provider — a card then draws without its "open" affordance. */
export function useChildRun(): ChildRunState | null {
  return useContext(ChildRunContext);
}
