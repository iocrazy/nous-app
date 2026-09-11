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

/** The step nodes for this coordinate, in document order.
 *
 * Both testids: the live step carries its own, and a link to the step being
 * executed right now is the normal case for a just-generated output.
 *
 * When the link names a `turn`, it is part of the match — a node's identity is
 * the pair (see `foldEvents`), so a two-turn run draws two "step 2"s and the
 * step alone would land on whichever comes last. No turn in the link (every
 * row registered before the builder sent one) keeps the old step-only rule. */
function stepNodes({ step, turn }: StepCoordinate): HTMLElement[] {
  const at = turn != null ? `[data-step="${step}"][data-turn="${turn}"]` : `[data-step="${step}"]`;
  return [
    ...document.querySelectorAll<HTMLElement>(
      `[data-testid="traj-step"]${at}, [data-testid="traj-step-live"]${at}`,
    ),
  ];
}

/**
 * Open every collapsed run-group card.
 *
 * A bounded-continuation dispatch writes one `agent_run` row per turn, and
 * `runGrouping` folds two or more consecutive rows into a card that starts
 * COLLAPSED — which renders no `run-group-body` at all, so the trajectory
 * underneath never mounts and the step node this function is hunting for does
 * not exist in the DOM. Polling alone would just time out: the answer is not
 * "wait longer", it is "the thing is folded".
 *
 * Each card is unfolded AT MOST ONCE per search, tracked by element identity:
 *
 * * a group the reader folds back stays folded — the search runs for up to ten
 *   seconds, and a card that springs open again on every 120ms pass, with
 *   nothing on screen saying why, is the page fighting its reader;
 * * a card that REMOUNTED collapsed is a different element, so it is unfolded
 *   again — that collapse is the thread re-rendering while its rows load, not
 *   a decision anybody made.
 *
 * A `WeakSet` because the entries are DOM nodes whose lifetime is React's.
 */
function expandRunGroups(seen: WeakSet<HTMLElement>): void {
  const folded = document.querySelectorAll<HTMLElement>(
    '[data-testid="run-group-toggle"][aria-expanded="false"]',
  );
  folded.forEach((toggle) => {
    if (seen.has(toggle)) return;
    seen.add(toggle);
    toggle.click();
  });
}

/** Expand + scroll, once the node is there. `false` means "not yet". */
function openStep(where: StepCoordinate): boolean {
  // The LAST match: the thread runs oldest-first, and several runs of one
  // issue each have a step N. The link carries no run id, so the newest run
  // that reached that step is the best answer available.
  const node = stepNodes(where).at(-1);
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

/** Where a lineage link points. `turn` is absent on rows registered before
 *  the link builder carried it — then the step alone is all there is. */
export interface StepCoordinate {
  step: number;
  turn?: number | null;
}

/**
 * Start chasing a step. Returns a canceller the caller MUST run on unmount —
 * a pending timer that fires into a torn-down tree is the leak this shape
 * exists to prevent.
 *
 * `onSettled` fires exactly once, when the step was actually opened. It is
 * what lets a caller tell "this link is spent" from "the search was cut short
 * before it found anything" — React's StrictMode cuts every effect short once
 * in development, and a caller that latched on *starting* would never retry.
 */
export function focusTrajectoryStep(
  where: StepCoordinate,
  onSettled?: () => void,
): () => void {
  const { step } = where;
  if (typeof document === 'undefined' || !Number.isInteger(step) || step < 0) {
    return () => undefined;
  }
  let timer: ReturnType<typeof setTimeout> | null = null;
  let cancelled = false;
  // Per search, not per module: a later deep link is entitled to unfold the
  // same cards again.
  const unfolded = new WeakSet<HTMLElement>();
  const deadline = Date.now() + DEADLINE_MS;
  const tick = () => {
    timer = null;
    if (cancelled) return;
    if (openStep(where)) {
      // Only NOW is the deep link spent. Reporting it any earlier would let a
      // caller mark the job done for a search that never found its step.
      onSettled?.();
      return;
    }
    // Not there yet — it may be folded rather than unrendered. Checked on
    // every failed pass because the thread re-renders while its rows load, but
    // each card is only ever clicked once (see `expandRunGroups`).
    expandRunGroups(unfolded);
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
