/**
 * A one-line pub/sub for "an issue was just paused / resumed" so the Task
 * Center's Paused section refetches right away instead of waiting for its
 * poll. Module-scoped on purpose: the card and the section share no parent
 * that owns both (ActiveTaskCard sits under FlowTaskList), so a prop chain
 * would thread through four components for one boolean.
 */
type Listener = (issueId: number) => void;

const listeners = new Set<Listener>();

export function notifyIssuePauseChanged(issueId: number): void {
  for (const l of Array.from(listeners)) {
    try {
      l(issueId);
    } catch (err) {
      // A bad subscriber never blocks the others.
      console.error('[issuePauseSignal] listener failed', err);
    }
  }
}

export function subscribeIssuePauseChanged(l: Listener): () => void {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}
