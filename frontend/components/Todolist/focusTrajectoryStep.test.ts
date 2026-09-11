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
    const toggle = document.createElement('button');
    toggle.setAttribute('data-testid', 'run-group-toggle');
    toggle.setAttribute('aria-expanded', 'false');
    const unfolded = vi.fn();
    toggle.addEventListener('click', unfolded);
    document.body.appendChild(toggle);
    const cancel = focusTrajectoryStep({ step: 9 });
    await tick(200);
    expect(unfolded).toHaveBeenCalled();
    cancel();
  });
});
