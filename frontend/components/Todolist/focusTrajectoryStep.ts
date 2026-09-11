/**
 * Open the trajectory step a lineage deep link named (harness 3a Task 8b).
 *
 * `issue_deep_link` appends `?step=N` to every URL it builds, and spec §4
 * promises the reader lands on THAT step. The page consumed `run`/`seq` (the
 * replay link) and dropped `step` on the floor, so an object's provenance
 * button opened the issue at the top and left the reader to scroll a run of
 * thirty steps looking for the one that made the thing they clicked from.
 *
 * Why the DOM and not a prop: the step nodes are rendered by
 * `TrajectoryRenderer`, which owns its expansion state privately, one instance
 * per run row, several rows deep inside the thread. Threading an "open this
 * one" prop down would mean the page knowing which run row owns the step —
 * which is exactly what the link does NOT say. This reuses the timeline's
 * existing jump mechanism (`scrollToRun` in the trajectory's own builtins does
 * the same `getElementById` + `scrollIntoView`), it does not add a second one.
 *
 * It POLLS because the step it wants does not exist yet at mount: the run row
 * fetches its transcript, folds it, and only then renders any step at all.
 * Giving up quietly at the deadline is the honest end state — a link may name
 * a step of a run this issue no longer shows, and a toast for that would fire
 * on every slow transcript too.
 */

const POLL_MS = 120;
const DEADLINE_MS = 10_000;

/** The step nodes for `step`, in document order. Both testids: the live step
 *  carries its own, and a link to the step being executed right now is the
 *  normal case for a just-generated output. */
function stepNodes(step: number): HTMLElement[] {
  return [
    ...document.querySelectorAll<HTMLElement>(
      `[data-testid="traj-step"][data-step="${step}"], [data-testid="traj-step-live"][data-step="${step}"]`,
    ),
  ];
}

/** Expand + scroll, once the node is there. `false` means "not yet". */
function openStep(step: number): boolean {
  // The LAST match: the thread runs oldest-first, and several runs of one
  // issue each have a step N. The link carries no run id, so the newest run
  // that reached that step is the best answer available.
  const node = stepNodes(step).at(-1);
  if (!node) return false;
  const toggle = node.querySelector<HTMLElement>('button[aria-expanded]');
  // Only ever opens. A live step is already expanded by the renderer, and
  // toggling it would close the one thing the link pointed at.
  if (toggle?.getAttribute('aria-expanded') === 'false') toggle.click();
  // jsdom has no layout, so the method is simply absent there.
  if (typeof node.scrollIntoView === 'function') {
    node.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
  return true;
}

/**
 * Start chasing `step`. Returns a canceller the caller MUST run on unmount —
 * a pending timer that fires into a torn-down tree is the leak this shape
 * exists to prevent.
 */
export function focusTrajectoryStep(step: number): () => void {
  if (typeof document === 'undefined' || !Number.isInteger(step) || step < 0) {
    return () => undefined;
  }
  let timer: ReturnType<typeof setTimeout> | null = null;
  let cancelled = false;
  const deadline = Date.now() + DEADLINE_MS;
  const tick = () => {
    timer = null;
    if (cancelled || openStep(step)) return;
    if (Date.now() >= deadline) return;
    timer = setTimeout(tick, POLL_MS);
  };
  tick();
  return () => {
    cancelled = true;
    if (timer) clearTimeout(timer);
    timer = null;
  };
}

export default focusTrajectoryStep;
