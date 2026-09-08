import { describe, expect, it, vi } from 'vitest';
import { notifyIssuePauseChanged, subscribeIssuePauseChanged } from './issuePauseSignal';

describe('issuePauseSignal', () => {
  it('delivers to subscribers until they unsubscribe, and a throwing one does not starve the rest', () => {
    const a = vi.fn(() => { throw new Error('bad subscriber'); });
    const b = vi.fn();
    const offA = subscribeIssuePauseChanged(a);
    const offB = subscribeIssuePauseChanged(b);
    const err = vi.spyOn(console, 'error').mockImplementation(() => {});
    notifyIssuePauseChanged(5);
    expect(a).toHaveBeenCalledWith(5);
    expect(b).toHaveBeenCalledWith(5);
    offA();
    notifyIssuePauseChanged(6);
    expect(a).toHaveBeenCalledTimes(1);
    expect(b).toHaveBeenCalledTimes(2);
    offB();
    err.mockRestore();
  });
});
