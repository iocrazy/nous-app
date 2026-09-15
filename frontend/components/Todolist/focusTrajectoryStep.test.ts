/**
 * 3a Task 8b fix round 1 — when is a `?step=` link SPENT?
 *
 * The page may only mark the anchor as handled once a step was actually
 * opened. Marking it on "we started looking" is what leaves the link dead
 * whenever a search is cut short before it finds anything — React's
 * StrictMode cuts every mount effect short once in development, and any
 * re-run that cancels the previous search would do the same in production.
 *
 * Driven against a hand-built DOM rather than a rendered page: the contract
 * under test is the module's own (find → open → report), and a component test
 * would hide the failing half behind React's scheduling.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

import { focusTrajectoryStep } from './focusTrajectoryStep';

function drawStep({ step, turn }: { step: number; turn?: number }): HTMLElement {
  const node = document.createElement('div');
  node.setAttribute('data-testid', 'traj-step');
  node.setAttribute('data-step', String(step));
  if (turn !== undefined) node.setAttribute('data-turn', String(turn));
  const head = document.createElement('button');
  head.setAttribute('aria-expanded', 'false');
  node.appendChild(head);
  document.body.appendChild(node);
  return node;
}

const tick = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** A run-group header that behaves like the real one: clicking it flips its
 *  own `aria-expanded`, the way React would. */
function drawRunGroupToggle(): { toggle: HTMLElement; unfolded: ReturnType<typeof vi.fn> } {
  const toggle = document.createElement('button');
  toggle.setAttribute('data-testid', 'run-group-toggle');
  toggle.setAttribute('aria-expanded', 'false');
  const unfolded = vi.fn(() => toggle.setAttribute('aria-expanded', 'true'));
  toggle.addEventListener('click', unfolded);
  document.body.appendChild(toggle);
  return { toggle, unfolded };
}

afterEach(() => {
  document.body.innerHTML = '';
});

describe('focusTrajectoryStep', () => {
  it('opens the step and reports settled', async () => {
    const node = drawStep({ step: 2 });
    const onSettled = vi.fn();
    const cancel = focusTrajectoryStep({ step: 2 }, onSettled);
    expect(node.querySelector('button')!.getAttribute('aria-expanded')).toBe('false');
    // The click is dispatched; the attribute only moves when React owns it, so
    // the observable here is the click itself.
    expect(onSettled).toHaveBeenCalledTimes(1);
    cancel();
  });

  it('waits for a step that has not rendered yet', async () => {
    const onSettled = vi.fn();
    const cancel = focusTrajectoryStep({ step: 3 }, onSettled);
    expect(onSettled).not.toHaveBeenCalled();
    drawStep({ step: 3 });
    await tick(300);
    expect(onSettled).toHaveBeenCalledTimes(1);
    cancel();
  });

  it('a cancelled search NEVER reports settled — the link is not spent', async () => {
    const onSettled = vi.fn();
    const cancel = focusTrajectoryStep({ step: 4 }, onSettled);
    cancel();
    drawStep({ step: 4 });
    await tick(300);
    expect(onSettled).not.toHaveBeenCalled();
  });

  it('matches on the (turn, step) pair when the link names a turn', async () => {
    const first = drawStep({ step: 2, turn: 1 });
    const second = drawStep({ step: 2, turn: 2 });
    const clicked: string[] = [];
    for (const [name, node] of [['turn1', first], ['turn2', second]] as const) {
      node.querySelector('button')!.addEventListener('click', () => clicked.push(name));
    }
    const cancel = focusTrajectoryStep({ step: 2, turn: 1 });
    expect(clicked).toEqual(['turn1']);
    cancel();
  });

  it('with no turn, the last step with that number wins', async () => {
    const first = drawStep({ step: 2, turn: 1 });
    const second = drawStep({ step: 2, turn: 2 });
    const clicked: string[] = [];
    for (const [name, node] of [['turn1', first], ['turn2', second]] as const) {
      node.querySelector('button')!.addEventListener('click', () => clicked.push(name));
    }
    const cancel = focusTrajectoryStep({ step: 2 });
    expect(clicked).toEqual(['turn2']);
    cancel();
  });

  it('unfolds a collapsed run-group while it searches', async () => {
    const { toggle, unfolded } = drawRunGroupToggle();
    const cancel = focusTrajectoryStep({ step: 9 });
    await tick(200);
    expect(unfolded).toHaveBeenCalled();
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    cancel();
  });

  it('unfolds each card ONCE — a group the reader folds back stays folded', async () => {
    // The search runs for up to ten seconds. Re-clicking every collapsed
    // toggle on every 120ms pass would mean a reader who folds a group back
    // during that window watches it spring open again, repeatedly, with
    // nothing on screen explaining why.
    const { toggle, unfolded } = drawRunGroupToggle();
    const cancel = focusTrajectoryStep({ step: 9 });
    expect(unfolded).toHaveBeenCalledTimes(1);
    // The reader folds it back.
    toggle.setAttribute('aria-expanded', 'false');
    await tick(400);
    expect(unfolded).toHaveBeenCalledTimes(1);
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    cancel();
  });

  it('unfolds a card that REMOUNTED collapsed, even after an earlier one', async () => {
    // A remount is a different element, and its collapsed state is not the
    // reader\'s decision — it is the thread re-rendering while rows load.
    const first = drawRunGroupToggle();
    const cancel = focusTrajectoryStep({ step: 9 });
    expect(first.unfolded).toHaveBeenCalledTimes(1);
    first.toggle.remove();
    const second = drawRunGroupToggle();
    await tick(400);
    expect(second.unfolded).toHaveBeenCalledTimes(1);
    cancel();
  });

  it('falls back to step-only when nothing carries the named turn — but not before half the deadline', async () => {
    // A transcript folded before `data-turn` existed — or a link naming a turn
    // this run never had. The turn is a refinement, never a precondition: the
    // new link must not be weaker than the one-key link it replaced.
    //
    // C11: the fallback is DELAYED, not removed. Taking it on the very first
    // pass hands the reader whichever same-numbered step mounted first, while
    // the one the link actually names is still loading.
    vi.useFakeTimers();
    try {
      const node = drawStep({ step: 2 });
      const clicked = vi.fn();
      node.querySelector('button')!.addEventListener('click', clicked);
      const cancel = focusTrajectoryStep({ step: 2, turn: 1 });
      expect(clicked).not.toHaveBeenCalled();
      vi.advanceTimersByTime(5_100);
      expect(clicked).toHaveBeenCalledTimes(1);
      cancel();
    } finally {
      vi.useRealTimers();
    }
  });

  it('C11: an exact node that arrives before half-time wins over the step-only one already there', async () => {
    // 深链与流式到达并存的那一刻：同号的旧步骤先挂载，链接真正指的那个还在
    // 路上。立刻放宽 = 把读者送到错误的回合上，而且是**静默**的。
    vi.useFakeTimers();
    try {
      const legacy = drawStep({ step: 2 });
      const hits: string[] = [];
      legacy.querySelector('button')!.addEventListener('click', () => hits.push('legacy'));
      const cancel = focusTrajectoryStep({ step: 2, turn: 7 });

      vi.advanceTimersByTime(2_000);
      expect(hits).toEqual([]); // 还在等精确节点

      const exact = drawStep({ step: 2, turn: 7 });
      exact.querySelector('button')!.addEventListener('click', () => hits.push('exact'));
      vi.advanceTimersByTime(200);
      expect(hits).toEqual(['exact']);
      cancel();
    } finally {
      vi.useRealTimers();
    }
  });

  it('C11: 半程过了精确节点仍没来，才退回 step-only；onSettled 也只在那时才报', async () => {
    vi.useFakeTimers();
    try {
      const legacy = drawStep({ step: 2 });
      const hits: string[] = [];
      legacy.querySelector('button')!.addEventListener('click', () => hits.push('legacy'));
      const onSettled = vi.fn();
      const cancel = focusTrajectoryStep({ step: 2, turn: 7 }, onSettled);

      vi.advanceTimersByTime(4_800);
      expect(hits).toEqual([]);
      expect(onSettled).not.toHaveBeenCalled();

      vi.advanceTimersByTime(400);
      expect(hits).toEqual(['legacy']);
      expect(onSettled).toHaveBeenCalledTimes(1);
      cancel();
    } finally {
      vi.useRealTimers();
    }
  });

  it('C11: 链接根本不带 turn 时，第一趟就落地——放宽只针对「有 turn 但没匹配上」', async () => {
    vi.useFakeTimers();
    try {
      const node = drawStep({ step: 2 });
      const clicked = vi.fn();
      node.querySelector('button')!.addEventListener('click', clicked);
      const cancel = focusTrajectoryStep({ step: 2 });
      expect(clicked).toHaveBeenCalledTimes(1);
      cancel();
    } finally {
      vi.useRealTimers();
    }
  });

  it('prefers the exact (turn, step) over the fallback when both could match', async () => {
    const legacy = drawStep({ step: 2 });
    const exact = drawStep({ step: 2, turn: 1 });
    const hits: string[] = [];
    legacy.querySelector('button')!.addEventListener('click', () => hits.push('legacy'));
    exact.querySelector('button')!.addEventListener('click', () => hits.push('exact'));
    const cancel = focusTrajectoryStep({ step: 2, turn: 1 });
    expect(hits).toEqual(['exact']);
    cancel();
  });
});
