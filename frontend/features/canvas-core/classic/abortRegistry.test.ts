import { afterEach, describe, expect, it } from 'vitest';

import {
  abortNode,
  beginAbortable,
  clearAbortController,
  clearAllAbortControllers,
  getAbortSignal,
  hasAbortController,
} from './abortRegistry';

afterEach(() => clearAllAbortControllers());

describe('abortRegistry', () => {
  it('beginAbortable registers a live signal for the node', () => {
    const controller = beginAbortable('n1');
    expect(controller.signal.aborted).toBe(false);
    expect(hasAbortController('n1')).toBe(true);
    expect(getAbortSignal('n1')).toBe(controller.signal);
  });

  it('beginAbortable aborts a prior in-flight controller for the same node', () => {
    const first = beginAbortable('n1');
    const second = beginAbortable('n1');
    expect(first.signal.aborted).toBe(true);
    expect(second.signal.aborted).toBe(false);
    expect(getAbortSignal('n1')).toBe(second.signal);
  });

  it('abortNode aborts the signal and clears the slot', () => {
    const controller = beginAbortable('n1');
    expect(abortNode('n1')).toBe(true);
    expect(controller.signal.aborted).toBe(true);
    expect(hasAbortController('n1')).toBe(false);
  });

  it('abortNode returns false when no run is in flight', () => {
    expect(abortNode('missing')).toBe(false);
  });

  it('clearAbortController drops the slot WITHOUT aborting', () => {
    const controller = beginAbortable('n1');
    clearAbortController('n1');
    expect(controller.signal.aborted).toBe(false);
    expect(hasAbortController('n1')).toBe(false);
  });
});
